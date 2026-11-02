"""Versioned rule retrieval and exports, derived from the shared product knowledge.

Search stays local. Only stable rule IDs are sent to Core, so questions, names
and credentials never become HTTP query strings or rule-search access logs.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from banking_knowledge import KNOWLEDGE_VERSION, TOPICS, capability_catalog, followup_catalog, knowledge_documents
from bank_planning.requests import RESOURCES, STATUSES

TOPIC_NAMES = {'general': '通用对话与事实', 'contacts': '联系人与收款人', 'transfer': '转账、预约与AA',
               'card': '卡片管理', 'limits': '卡片额度', 'security': '身份、确认与验证', 'cross_scene': '生日联动',
               'tasks': '待办与撤销', 'dispute': '可疑交易与争议', 'wealth': '理财与测评', 'subscription': '订阅与代扣'}
ALIASES = {'general': '余额 事实 数据来源 对象 指代 插问 持久化 写盘失败 回执', 'contacts': '联系人 通讯录 常用收款人 已保存',
           'transfer': '转账 汇款 预约 AA 收款 到账', 'card': '卡片 银行卡 解冻 冻结 挂失 渠道',
           'limits': '额度 限额 日额度 卡片额度 无限 调额 额度限制', 'security': '确认 验证 验证码 签名 MFA 设备 权限 飞书',
           'cross_scene': '生日 订花 鲜花 蛋糕 预算 锁资', 'tasks': '待办 任务 撤销 暂停 人工处理',
           'dispute': '争议 工单 可疑 盗刷 核对', 'wealth': '理财 产品 持仓 申购 赎回 风险测评',
           'subscription': '订阅 会员 代扣 续费 扣款 视频 Apple'}
REFERENCES = {
    'general': ['bank_agent/understanding.py', 'bank_agent/query_focus.py', 'bank_agent/answering.py', 'bank_core/routes/accounts.py', 'bank_core/persistence.py'],
    'contacts': ['bank_core/routes/contacts.py', 'bank_core/contacts.py', 'bank_core/ledger.py', 'bank_core/routes/tasks.py', 'bank_agent/query_focus.py', 'android/app/src/main/java/com/aibanking/agent/ui/chat/ChatContacts.kt'],
    'transfer': ['bank_core/routes/approvals.py', 'bank_core/routes/execution.py', 'bank_core/payments.py'],
    'card': ['bank_core/cards.py', 'bank_core/routes/approvals.py', 'bank_core/routes/execution.py'],
    'limits': ['bank_core/cards.py', 'bank_core/operations.py', 'bank_core/routes/execution.py'],
    'security': ['bank_security/authorization.py', 'bank_security/sessions.py', 'bank_agent/conversation_approvals.py'],
    'cross_scene': ['bank_core/routes/execution.py', 'bank_core/routes/tasks.py', 'scheduler.py'],
    'tasks': ['bank_core/routes/tasks.py', 'bank_core/undo.py', 'bank_core/task_scheduler.py'],
    'dispute': ['bank_core/routes/accounts.py', 'bank_core/routes/execution.py'],
    'wealth': ['bank_core/routes/wealth.py', 'bank_core/routes/execution.py', 'bank_core/wealth_catalogue.py', 'bank_core/wealth_data.py', 'bank_agent/risk_quiz.py'],
    'subscription': ['bank_core/routes/subscriptions.py', 'bank_core/routes/execution.py', 'bank_agent/query_focus.py'],
}
FIELD_NAMES = {'target': '对象或编号', 'amount': '金额', 'participants': '参与人', 'product_ids': '产品编号',
               'scheduled_at': '预约日期时间', 'event_at': '事件日期时间', 'reminder_at': '提醒时间',
               'card_channel': '支付渠道', 'undo_kind': '撤销类型', 'loss_tolerance': '亏损承受能力（旧字段）',
               'experience': '投资经验（旧字段）', 'horizon_years': '投资期限（旧字段）'}


def _intent_topic(intent: str) -> str:
    if intent.startswith('SUBSCRIPTION_'):
        return 'subscription'
    if intent.startswith('WEALTH_'):
        return 'wealth'
    if intent.startswith('CARD_'):
        return 'limits' if intent == 'CARD_LIMIT' else 'card'
    if intent in {'TRANSFER', 'SCHEDULED_TRANSFER', 'SPLIT_BILL'}:
        return 'transfer'
    return {'CROSS_SCENE_LOCK': 'cross_scene', 'FRAUD_DISPUTE': 'dispute', 'DISPUTE_QUERY': 'dispute',
            'PASSWORD_CHANGE': 'security', 'UNDO_OPERATION': 'tasks'}.get(intent, 'general')


def rule_documents() -> list[dict[str, Any]]:
    documents = [{**item, 'title': TOPIC_NAMES[topic] + '：' + item['text'].split('：')[0][:24],
                  'kind': 'policy', 'code_refs': REFERENCES[topic]}
                 for topic in sorted(TOPICS) for item in knowledge_documents(topic)]
    for item in capability_catalog():
        intent, fields = item['intent'], item['required_fields']
        topic = _intent_topic(intent)
        risk = ('无需办理确认的查询或对话' if item['baseline'] == 'GREEN' else '需确认和强验证' if item['baseline'] == 'RED'
                else '需核对确认，实际风险及验证要求以 Core 预检为准')
        if intent == 'UNKNOWN':
            risk = '澄清或准确说明不支持，不发起相近操作'
        elif intent == 'CARD_CHANNEL':
            risk = '关闭渠道需确认，开通需强验证，以 Core 审批为准'
        elif intent == 'WEALTH_RISK_ASSESSMENT':
            risk = '按本人真实答案完成问卷并保存档案，不代表已申购'
        names = '、'.join(FIELD_NAMES.get(field, field) for field in fields) or '按问题明确对象；不能推断本人实时状态'
        documents.append({'id': 'capability.' + intent, 'topic': topic, 'title': item['description'],
                          'text': f"能力：{item['description']}。解析字段提示：{names}。协议：{risk}。字段提示不是完整办理条件，主题规则及 Core 的实际数据、风险与参数校验仍需满足。",
                          'source': f'rules://{KNOWLEDGE_VERSION}/capability/{intent}', 'kind': 'capability',
                          'intent': intent, 'required_fields': fields, 'baseline': item['baseline'],
                          'code_refs': ['bank_planning/intents.py', *REFERENCES[topic]]})
    return documents


def rule_index() -> list[dict[str, str]]:
    """A complete semantic directory; no account facts or permission grants."""
    return [{key: item[key] for key in ('id', 'topic', 'title')} for item in rule_documents()]


@lru_cache(maxsize=1)
def implementation_manifest() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    files = {'banking_rules.py', 'banking_knowledge.py', 'bank_planning/intents.py',
             'intent_language.py', 'bank_agent/extraction.py', 'bank_agent/dialogue_state.py', 'bank_agent/dialogue.py', 'bank_agent/pending_questions.py',
             'bank_planning/requests.py', 'bank_agent/parsing.py', 'bank_agent/consultation.py',
             'bank_agent/goal_fidelity.py', 'workflow_runtime.py', 'model_config.py',
             'bank_agent/evidence.py', 'bank_agent/evidence_session.py', 'bank_agent/goal_answering.py', 'bank_agent/answer_presentation.py',
             'bank_agent/dialogue_commands.py', 'bank_agent/entities.py', 'bank_agent/computations.py', 'bank_agent/answer_repair.py', 'bank_agent/prompt_preferences.py',
             'bank_agent/nodes/intent.py', 'bank_agent/nodes/responder.py', 'bank_agent/nodes/executor.py',
             *(path for paths in REFERENCES.values() for path in paths)}
    return {path: hashlib.sha256((root / path).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for path in sorted(files)}


@lru_cache(maxsize=1)
def rules_version() -> str:
    payload = json.dumps({'documents': rule_documents(), 'followups': followup_catalog(), 'implementation': implementation_manifest()},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def select_rules(ids: list[str]) -> list[dict[str, Any]]:
    if not ids or len(ids) > 20 or len(set(ids)) != len(ids):
        raise ValueError('INVALID_RULE_IDS')
    catalog = {item['id']: item for item in rule_documents()}
    if any(not isinstance(value, str) or value not in catalog for value in ids):
        raise ValueError('UNKNOWN_RULE_ID')
    return [catalog[value] for value in ids]


def _tokens(text: str) -> Counter[str]:
    text = text.casefold().replace('扣钱', '扣款').replace('扣费', '扣款')
    tokens = re.findall(r'[a-z][a-z0-9_-]*|\d+(?:\.\d+)?', text)
    for phrase in re.findall(r'[一-龥]+', text):
        tokens.extend(phrase[index:index+2] for index in range(len(phrase)-1))
    return Counter(tokens)


def search_rules(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 700 or type(limit) is not int or not 1 <= limit <= 6:
        raise ValueError('INVALID_RULE_QUERY')
    documents = rule_documents()
    vectors = [_tokens(item['title'] + ' ' + item['text'] + ' ' + ALIASES[item['topic']]) for item in documents]
    frequency = Counter(token for vector in vectors for token in vector)
    # Navigation words are shared across domains and should not outrank the
    # business object (e.g. "查看我的理财持仓" must not retrieve contact navigation).
    asked = _tokens(re.sub(r'如何|怎么|查看|我的|一下|帮我|我想问|请问|本产品|这款产品|咱们产品|本行|本演示|是怎样的|怎样的|是怎样', ' ', query))
    scored = []
    for item, vector in zip(documents, vectors):
        score = sum(math.log(1 + (len(documents) - frequency[token] + .5)/(frequency[token] + .5))
                    * min(vector[token], 2) / (1 + sum(vector.values())/200)
                    for token in asked if token in vector)
        title = _tokens(item['title'])
        score += sum(math.log(1 + (len(documents) - frequency[token] + .5)/(frequency[token] + .5))
                     * 2 for token in asked if token in title)
        if score:
            scored.append((score, item['id'], item))
    ranked = sorted(scored, key=lambda row: (-row[0], row[1]))
    threshold = ranked[0][0] * .35 if ranked else 0
    return [item for score, _, item in ranked if score >= threshold][:limit]


def export_rules() -> dict[str, Any]:
    return {'schema_version': 1, 'knowledge_version': KNOWLEDGE_VERSION, 'rules_version': rules_version(),
            'implementation': implementation_manifest(), 'rules': rule_documents(), 'followups': followup_catalog(),
            'request_protocol': {'version': 1, 'resources': RESOURCES, 'coverage_statuses': sorted(STATUSES),
                                 'calculation_operations': ['difference', 'sum', 'count'],
                                 'discussion_commands': ['RESUME', 'FILL', 'CORRECT', 'PAUSE', 'CANCEL']}}


def render_rulebook() -> str:
    lines = ['# 银行业务规则与能力目录', '', f'知识版本：`{KNOWLEDGE_VERSION}`；规则摘要：`{rules_version()}`。', '',
             '> [!NOTE]',
             '> 本页由规则实现自动生成，供开发者阅读和模型同源检索。它描述安言的 Mock 银行，不是现实银行政策。', '',
             '[文档中心](README.md) · [同源 JSON](银行业务规则.json) · [核心算法](核心算法说明.md)', '',
             '## 阅读这份目录', '',
             '能力表帮助定位意图与字段，主题条款说明业务前提和限制，末尾列出推荐问题范围。每条规则具有稳定编号和实现来源。', '',
             '请修改 `banking_knowledge.py` 或实际能力实现后重新导出，不直接编辑本页或 JSON。导出一致不等于语义与执行已经通过审查。', '',
             '模型先检索规则判断能力与正确数据源，再读取本人当前记录；规则文本不能证明余额、联系人、订阅、工单或执行结果。', '',
             '用户问题、历史对话和工具内容不能修改这些权限。规则不授予执行能力；Core 的身份、确认、MFA、一次性授权、幂等与持久提交仍独立生效。', '',
             '## 如何查询和维护', '',
             '- 人阅读本说明；模型查看完整条款目录，用同源 JSON 或 `rules(query)` / `rules(rule_ids)` 检索。每条结果有稳定编号、版本和实现来源。',
             '- 自然语言匹配在本地进行，向 Core 只发送规则编号，不把问题、姓名或凭据放进检索 URL。',
             '- 规则未覆盖的内容应说明缺口；动态额度、验证升级、产品限制及账户状态以当前 Core 记录或审批为准。',
             '- 修改规则或能力后运行 `python scripts/export_banking_rules.py`，提交前运行同一命令加 `--check`。生成一致只证明同源，不能代替语义审查和执行测试。', '',
             '- 摘要同时覆盖条款、能力目录及引用实现文件；JSON 记录实现摘要。代码变化后须重新生成并复核条款是否仍与实现一致。', '',
             '## 多诉求查询与补查', '',
             '理解模型输出最多五项本轮诉求，分别记录行为、资源、知识主题、对象及字段；对象须来自原文或同主体已核验实体。查询和解释走共享取证与逐项核验。', '',
             '通用咨询有十二类固定只读工具，新增产品、持仓、测评档案和本人任务读取；不开放任意 URL、身份、HTTP 方法或免确认订阅入口。', '',
             '多诉求读取预算按数量计算，上限十次读取，初次与修复共用；最多三轮取证规划、三次作答。缺证据和核验反馈可以驱动补查，重复读取去重，预算耗尽保留逐项缺口。旧协议兼容路径仍为五次读取、两轮规划。', '',
             '逐项状态区分已回答、缺数据、需澄清、需确认、不支持和工具失败；部分完成不标为全部成功。金额继续引用绑定字段，订阅实际记录仍需原确认，资金回执不由生成器改写。', '',
             '## 当前能力目录', '', '下表是解析字段提示，不是完整办理条件。空缺项不表示可以直接执行；多卡、测评、余额、产品适当性与授权条件仍由主题规则和 Core 校验。风险测评优先使用问卷，旧字段仅用于兼容路径。', '',
             '| 意图 | 能力 | 解析字段提示 | 权限基线 |', '| --- | --- | --- | --- |']
    for item in capability_catalog():
        fields = '、'.join(FIELD_NAMES.get(field, field) for field in item['required_fields']) or '按问题明确对象'
        lines.append(f"| `{item['intent']}` | {item['description']} | {fields} | {item['baseline']}，以 Core 为准 |")
    for topic in sorted(TOPICS):
        lines += ['', '## ' + TOPIC_NAMES[topic], '']
        for item in rule_documents():
            if item['topic'] != topic or item['kind'] != 'policy':
                continue
            lines += [f"### {item['id']}", '', item['text'], '']
        refs = '、'.join(f'[{path}](../{path})' for path in REFERENCES[topic])
        lines += ['实现依据：' + refs + '。', '']
    lines += ['', '## 推荐问题的可回答范围', '',
              '推荐仅从服务端登记的问题及有据绑定的对象中选择，模型负责选择、排序和独立核验，不能自行改写按钮。排除项即使能回答“不支持”，也不主动推荐。实际读取仍需当前会话及相应确认；用户原有缺槽任务仅可补充必要字段，不代选金额。自由输入不受推荐问题集合限制。', '',
              '| 编号 | 可回答范围 | 不主动推荐的方向 |', '| --- | --- | --- |']
    for item in followup_catalog():
        lines.append(f"| `{item['id']}` | {item['coverage']} | {item['excluded']} |")
    return '\n'.join(lines).rstrip() + '\n'
