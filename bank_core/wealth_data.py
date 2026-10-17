"""Wealth product catalogue, NAV fixtures and the suitability (risk grade) tables."""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Mapping, Optional

from bank_core.ledger import register_collections

# Additional mock products make the other competition scenarios executable
# without pretending that a real bank is connected.  These records are
# deterministic fixtures; all writes still pass through the same authorization
# gateway used by transfers.
# 演示产品货架：五款虚构产品覆盖 R1-R5 完整风险谱系。产品、管理人、托管行、
# 基金经理与持仓名称全部为虚构，业绩比较基准不是收益承诺。
WEALTH_PRODUCTS: list[dict[str, Any]] = [
    {
        "product_id": "LX00118",
        "name": "零星宝·活钱管家",
        "category": "现金管理类",
        "risk_level": "R1",
        "benchmark": "1.85% ~ 2.20%（7日年化）",
        "annualized_yield": 0.0205,
        "min_amount": 1.0,
        "liquidity": "T+0 快速赎回/T+1 确认",
        "term": "随存随取（工作日 T+1 确认，单日快速赎回最高 5 万元）",
        "underlying": "银行存款、同业存单与剩余期限 397 天以内的高等级短期债券",
        "target_clients": "存放日常零花、工资备用金，要求随时可取的保守型客户",
    },
    {
        "product_id": "HQ30060",
        "name": "恒泉稳利·60天持有",
        "category": "固定收益类",
        "risk_level": "R2",
        "benchmark": "2.40% ~ 2.90%（年化）",
        "annualized_yield": 0.0265,
        "min_amount": 1000.0,
        "liquidity": "60 天最短持有",
        "term": "每笔申购持有满 60 天后可在任一工作日赎回",
        "underlying": "不低于 85% 投资国债、政策性金融债与 AAA 级信用债，其余为货币市场工具",
        "target_clients": "有两三个月闲钱、希望收益高于活期且能接受短期锁定的稳健型客户",
    },
    {
        "product_id": "SG20270",
        "name": "双桂平衡·股债270天",
        "category": "固收+混合类",
        "risk_level": "R3",
        "benchmark": "3.20% ~ 4.60%（年化）",
        "annualized_yield": 0.039,
        "min_amount": 10000.0,
        "liquidity": "270 天定开",
        "term": "每 270 天开放一次申赎",
        "underlying": "75% ~ 85% 投资中高等级债券打底，15% ~ 25% 配置红利与宽基指数权益资产",
        "target_clients": "能接受一定净值波动、希望在债券打底的同时分享权益收益的平衡型客户",
    },
    {
        "product_id": "YH50365",
        "name": "远航智造·产业精选1年",
        "category": "权益类",
        "risk_level": "R4",
        "benchmark": "宽基股票指数收益率×75% + 综合债券指数收益率×25%",
        "annualized_yield": None,
        "min_amount": 50000.0,
        "liquidity": "365 天封闭",
        "term": "365 天封闭运作，到期一次性开放赎回",
        "underlying": "不低于 75% 投资于股票资产，重点布局智能制造、工业软件与新材料等成长行业",
        "target_clients": "能承受较大本金波动、追求长期资本增值的进取型客户",
    },
    {
        "product_id": "XH70730",
        "name": "星汉·全球多策略2年",
        "category": "另类投资类",
        "risk_level": "R5",
        "benchmark": "无业绩比较基准（净值型，可能出现较大亏损）",
        "annualized_yield": None,
        "min_amount": 100000.0,
        "liquidity": "730 天封闭",
        "term": "730 天封闭运作，期间不可赎回",
        "underlying": "全球股票、商品与股指期货等衍生品的多策略组合，可使用不超过 1.4 倍杠杆",
        "target_clients": "具备丰富投资经验、能承受本金大幅亏损、追求高收益的激进型客户",
    },
]
WEALTH_CATALOGUE: list[dict[str, Any]] = deepcopy(WEALTH_PRODUCTS)

_COMPANY = "星河理财有限公司"
_INSTITUTION = {"institution_founded_years": 18, "institution_aum_billion": 8650.4, "institution_rating": "综合评级 AA+（演示）"}
_NOTICE = "本页面为虚构的演示行情快照，产品与机构均不存在，不构成投资建议。"

# 详情页使用的静态行情快照。数值均为虚构演示数据，明确标注快照日期，
# 不代表实时行情、历史实际表现或未来收益，也不参与任何交易决策与记账。
_WEALTH_DETAIL_FIXTURES: dict[str, dict[str, Any]] = {
    "LX00118": {
        "latest_nav": 1.0187, "daily_change_pct": 0.01,
        "annual_return_pct": 2.08, "recent_month_return_pct": 0.16, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 0.04, "benchmark_return_pct": 0.04, "peer_average_pct": 0.04, "rank": "21/92"},
            {"period": "近1月", "product_return_pct": 0.16, "benchmark_return_pct": 0.15, "peer_average_pct": 0.15, "rank": "26/92"},
            {"period": "近3月", "product_return_pct": 0.50, "benchmark_return_pct": 0.47, "peer_average_pct": 0.46, "rank": "24/92"},
            {"period": "近6月", "product_return_pct": 1.02, "benchmark_return_pct": 0.96, "peer_average_pct": 0.94, "rank": "22/90"},
            {"period": "近1年", "product_return_pct": 2.08, "benchmark_return_pct": 1.95, "peer_average_pct": 1.90, "rank": "19/88"},
            {"period": "成立以来", "product_return_pct": 6.84, "benchmark_return_pct": 6.10, "peer_average_pct": 5.95, "rank": "14/70"},
        ],
        "analysis_metrics": [
            {"label": "净值平稳", "score": 5, "value": "波动率 0.04%", "description": "几乎没有回撤"},
            {"label": "活期替代", "score": 5, "value": "现金管理", "description": "适合工资与备用金"},
            {"label": "取用灵活", "score": 5, "value": "T+0 快赎", "description": "单日快速赎回最高 5 万元"},
            {"label": "风险收益比", "score": 4, "value": "回撤 0.00%", "description": "收益不高但非常稳定"},
        ],
        "manager": {
            "name": "苏亦然", "title": "现金管理部 投资经理", "tenure_since": "2023-02-20",
            "experience_years": 7.5, "managed_scale_billion": 42.3, "annualized_yield_pct": 2.21,
            "tenure_return_pct": 6.84, "avatar_url": "https://example.com/manager_suyiran.png",
            "bio": "虚构人物。7 年货币市场与短久期债券交易经验，注重流动性与信用安全",
        },
        "asset_allocation": [
            {"name": "银行存款", "percentage": 40.0},
            {"name": "同业存单", "percentage": 35.0},
            {"name": "短期债券", "percentage": 20.0},
            {"name": "现金及其他", "percentage": 5.0},
        ],
        "top_holdings": [
            {"name": "26云岭银行同业存单015", "category": "同业存单", "percentage": 13.6, "code": "Z260015", "daily_change_pct": 0.01},
            {"name": "26海澄银行同业存单032", "category": "同业存单", "percentage": 11.9, "code": "Z260032", "daily_change_pct": 0.01},
            {"name": "26北辰银行同业存单008", "category": "同业存单", "percentage": 10.4, "code": "Z260008", "daily_change_pct": 0.00},
            {"name": "26星海发展银行债01", "category": "政策金融债", "percentage": 8.2, "code": "Z260101", "daily_change_pct": 0.02},
            {"name": "26南屿银行同业存单021", "category": "同业存单", "percentage": 7.1, "code": "Z260021", "daily_change_pct": 0.01},
        ],
        "established_at": "2023-02-20", "scale_billion": 38.5, "manager_company": _COMPANY,
        "custodian": "云岭银行股份有限公司", "fund_type": "现金管理类 · 货币型", "dividend_mode": "每日结转 / 红利再投资",
        "original_fee_rate": 0.30, "discount_fee_rate": 0.00, "fee_rate": 0.00,
        "rating_agency": "演示评级 ★★★★☆", "award_title": "演示奖项 · 年度活钱管理",
        "promo_title": "活钱管家 每日计息", "promo_subtitle": "1 元起购，快速赎回实时到账",
        **_INSTITUTION,
        "purchase_rule": "1元起购，工作日 00:00-15:00 申请", "redemption_rule": "工作日支持快速赎回，单日上限5万元",
        "confirm_time": "T+1", "summary": "替代活期存款的超低波动现金管理产品，每日计算收益并结转份额。",
        "announcement": _NOTICE,
    },
    "HQ30060": {
        "latest_nav": 1.0452, "daily_change_pct": 0.03,
        "annual_return_pct": 2.71, "recent_month_return_pct": 0.21, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 0.05, "benchmark_return_pct": 0.05, "peer_average_pct": 0.05, "rank": "17/81"},
            {"period": "近1月", "product_return_pct": 0.21, "benchmark_return_pct": 0.20, "peer_average_pct": 0.19, "rank": "20/81"},
            {"period": "近3月", "product_return_pct": 0.66, "benchmark_return_pct": 0.63, "peer_average_pct": 0.60, "rank": "18/81"},
            {"period": "近6月", "product_return_pct": 1.33, "benchmark_return_pct": 1.28, "peer_average_pct": 1.22, "rank": "16/79"},
            {"period": "近1年", "product_return_pct": 2.71, "benchmark_return_pct": 2.62, "peer_average_pct": 2.50, "rank": "15/76"},
            {"period": "成立以来", "product_return_pct": 9.85, "benchmark_return_pct": 9.10, "peer_average_pct": 8.70, "rank": "11/60"},
        ],
        "analysis_metrics": [
            {"label": "纯债打底", "score": 4, "value": "中短久期", "description": "以利率债和 AAA 信用债为主"},
            {"label": "回撤控制", "score": 5, "value": "0.21%", "description": "近一年最大回撤很小"},
            {"label": "持有体验", "score": 4, "value": "60 天", "description": "满 60 天后随时可赎回"},
            {"label": "风险收益比", "score": 4, "value": "波动率 0.38%", "description": "收益略高于现金类，波动很低"},
        ],
        "manager": {
            "name": "顾清和", "title": "固定收益部 资深投资经理", "tenure_since": "2022-08-08",
            "experience_years": 10.0, "managed_scale_billion": 27.6, "annualized_yield_pct": 2.93,
            "tenure_return_pct": 9.85, "avatar_url": "https://example.com/manager_guqinghe.png",
            "bio": "虚构人物。10 年债券投研经验，擅长利率债久期管理与信用风险甄别",
        },
        "asset_allocation": [
            {"name": "利率债", "percentage": 45.0},
            {"name": "信用债", "percentage": 40.0},
            {"name": "货币工具", "percentage": 10.0},
            {"name": "现金及其他", "percentage": 5.0},
        ],
        "top_holdings": [
            {"name": "26附息国债示例07", "category": "记账式国债", "percentage": 15.2, "code": "Z261007", "daily_change_pct": 0.03},
            {"name": "26星海发展银行债03", "category": "政策金融债", "percentage": 12.4, "code": "Z260103", "daily_change_pct": 0.04},
            {"name": "25北辰电力MTN002", "category": "AAA级中票", "percentage": 9.6, "code": "Z252002", "daily_change_pct": 0.04},
            {"name": "26海澄交通债01", "category": "AAA级信用债", "percentage": 8.3, "code": "Z262101", "daily_change_pct": 0.02},
            {"name": "26附息国债示例11", "category": "记账式国债", "percentage": 6.9, "code": "Z261011", "daily_change_pct": 0.03},
        ],
        "established_at": "2022-08-08", "scale_billion": 21.4, "manager_company": _COMPANY,
        "custodian": "海澄银行股份有限公司", "fund_type": "固定收益类 · 纯债型", "dividend_mode": "现金分红 / 红利再投资",
        "original_fee_rate": 0.60, "discount_fee_rate": 0.06, "fee_rate": 0.15,
        "rating_agency": "演示评级 ★★★★☆", "award_title": "演示奖项 · 稳健固收",
        "promo_title": "持有 60 天 稳稳的", "promo_subtitle": "申购费率1折，满 60 天赎回免费",
        **_INSTITUTION,
        "purchase_rule": "1000元起购，工作日 00:00-15:00 申请", "redemption_rule": "每笔持有满60天后可随时赎回",
        "confirm_time": "T+1", "summary": "以高等级债券为主、持有期短的稳健型纯债产品。",
        "announcement": _NOTICE,
    },
    "SG20270": {
        "latest_nav": 1.1638, "daily_change_pct": 0.22,
        "annual_return_pct": 7.85, "recent_month_return_pct": 1.12, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 0.31, "benchmark_return_pct": 0.18, "peer_average_pct": 0.22, "rank": "33/310"},
            {"period": "近1月", "product_return_pct": 1.12, "benchmark_return_pct": 0.62, "peer_average_pct": 0.75, "rank": "41/310"},
            {"period": "近3月", "product_return_pct": 2.95, "benchmark_return_pct": 1.70, "peer_average_pct": 2.05, "rank": "38/305"},
            {"period": "近6月", "product_return_pct": -0.84, "benchmark_return_pct": -1.10, "peer_average_pct": -1.65, "rank": "47/298"},
            {"period": "近1年", "product_return_pct": 7.85, "benchmark_return_pct": 5.20, "peer_average_pct": 5.90, "rank": "36/285"},
            {"period": "成立以来", "product_return_pct": 16.38, "benchmark_return_pct": 11.40, "peer_average_pct": 12.10, "rank": "28/240"},
        ],
        "analysis_metrics": [
            {"label": "股债平衡", "score": 4, "value": "债 80% / 股 20%", "description": "债券打底，权益增强"},
            {"label": "红利风格", "score": 4, "value": "高股息", "description": "权益部分偏好红利与宽基"},
            {"label": "回撤控制", "score": 3, "value": "3.60%", "description": "近一年最大回撤可控"},
            {"label": "风险收益比", "score": 4, "value": "夏普 1.32", "description": "中等风险下收益较好"},
        ],
        "manager": {
            "name": "韩知远", "title": "多资产配置部 投资总监", "tenure_since": "2021-11-01",
            "experience_years": 13.0, "managed_scale_billion": 19.8, "annualized_yield_pct": 6.42,
            "tenure_return_pct": 16.38, "avatar_url": "https://example.com/manager_hanzhiyuan.png",
            "bio": "虚构人物。13 年多资产配置经验，擅长股债再平衡与回撤控制",
        },
        "asset_allocation": [
            {"name": "债券资产", "percentage": 77.0},
            {"name": "股票权益", "percentage": 18.5},
            {"name": "现金及其他", "percentage": 4.5},
        ],
        "top_holdings": [
            {"name": "26附息国债示例09", "category": "利率债", "percentage": 16.4, "code": "Z261009", "daily_change_pct": 0.04},
            {"name": "26星海发展银行债05", "category": "政策金融债", "percentage": 13.1, "code": "Z260105", "daily_change_pct": 0.05},
            {"name": "示例红利低波指数", "category": "红利指数", "percentage": 6.2, "code": "Z300201", "daily_change_pct": 0.58},
            {"name": "晨光精密", "category": "制造业权益", "percentage": 3.8, "code": "Z600118", "daily_change_pct": 1.24},
            {"name": "蓝湾公用", "category": "公用事业权益", "percentage": 3.1, "code": "Z600356", "daily_change_pct": 0.42},
        ],
        "established_at": "2021-11-01", "scale_billion": 12.7, "manager_company": _COMPANY,
        "custodian": "北辰银行股份有限公司", "fund_type": "固收+混合类 · 中风险", "dividend_mode": "现金分红 / 红利再投资",
        "original_fee_rate": 1.00, "discount_fee_rate": 0.10, "fee_rate": 0.60,
        "rating_agency": "演示评级 ★★★★☆", "award_title": "演示奖项 · 平衡配置",
        "promo_title": "股债平衡 稳中求进", "promo_subtitle": "申购费率1折，每 270 天开放一次",
        **_INSTITUTION,
        "purchase_rule": "10000元起购，开放期内申请", "redemption_rule": "每270天开放一次赎回",
        "confirm_time": "T+1", "summary": "债券打底、少量配置红利与宽基权益的平衡型产品，兼顾回撤与收益。",
        "announcement": _NOTICE,
    },
    "YH50365": {
        "latest_nav": 1.3125, "daily_change_pct": 0.86,
        "annual_return_pct": 15.32, "recent_month_return_pct": 2.74, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 1.05, "benchmark_return_pct": 0.72, "peer_average_pct": 0.80, "rank": "14/56"},
            {"period": "近1月", "product_return_pct": 2.74, "benchmark_return_pct": 1.95, "peer_average_pct": 2.10, "rank": "13/56"},
            {"period": "近3月", "product_return_pct": 6.18, "benchmark_return_pct": 4.40, "peer_average_pct": 4.95, "rank": "12/55"},
            {"period": "近6月", "product_return_pct": -4.36, "benchmark_return_pct": -3.20, "peer_average_pct": -3.85, "rank": "31/54"},
            {"period": "近1年", "product_return_pct": 15.32, "benchmark_return_pct": 11.80, "peer_average_pct": 12.40, "rank": "11/52"},
            {"period": "成立以来", "product_return_pct": 31.25, "benchmark_return_pct": 22.60, "peer_average_pct": 24.10, "rank": "8/44"},
        ],
        "analysis_metrics": [
            {"label": "成长风格", "score": 4, "value": "智能制造", "description": "聚焦工业软件与新材料"},
            {"label": "进攻弹性", "score": 4, "value": "超额明显", "description": "结构性行情中表现突出"},
            {"label": "回撤幅度", "score": 2, "value": "12.80%", "description": "波动大，需要长期持有"},
            {"label": "风险收益比", "score": 3, "value": "夏普 0.96", "description": "高风险对应较高收益"},
        ],
        "manager": {
            "name": "陆北川", "title": "权益投资部 董事总经理", "tenure_since": "2020-05-18",
            "experience_years": 15.0, "managed_scale_billion": 14.2, "annualized_yield_pct": 11.85,
            "tenure_return_pct": 31.25, "avatar_url": "https://example.com/manager_lubeichuan.png",
            "bio": "虚构人物。15 年权益投研经验，长期跟踪先进制造与工业自动化产业链",
        },
        "asset_allocation": [
            {"name": "权益资产", "percentage": 78.0},
            {"name": "债券资产", "percentage": 15.0},
            {"name": "现金及其他", "percentage": 7.0},
        ],
        "top_holdings": [
            {"name": "启明智造", "category": "工业自动化", "percentage": 8.6, "code": "Z688301", "daily_change_pct": 2.35},
            {"name": "远峰半导体", "category": "半导体设备", "percentage": 8.1, "code": "Z688512", "daily_change_pct": 3.10},
            {"name": "曜石材料", "category": "新材料", "percentage": 7.4, "code": "Z300467", "daily_change_pct": 1.42},
            {"name": "云图工业软件", "category": "工业软件", "percentage": 6.8, "code": "Z688725", "daily_change_pct": 2.86},
            {"name": "晨光精密", "category": "精密制造", "percentage": 6.1, "code": "Z600118", "daily_change_pct": 1.24},
        ],
        "established_at": "2020-05-18", "scale_billion": 6.3, "manager_company": _COMPANY,
        "custodian": "南屿银行股份有限公司", "fund_type": "权益类 · 行业主题型", "dividend_mode": "现金分红 / 红利再投资",
        "original_fee_rate": 1.50, "discount_fee_rate": 0.15, "fee_rate": 1.20,
        "rating_agency": "演示评级 ★★★☆☆", "award_title": "演示奖项 · 产业成长",
        "promo_title": "远航智造 长期成长", "promo_subtitle": "申购费率1折，聚焦智能制造",
        **_INSTITUTION,
        "purchase_rule": "50000元起购，募集期内申请", "redemption_rule": "封闭期满后开放赎回",
        "confirm_time": "T+1", "summary": "面向长期资金、聚焦智能制造的高波动权益类产品。",
        "announcement": _NOTICE,
    },
    "XH70730": {
        "latest_nav": 1.4870, "daily_change_pct": -1.28,
        "annual_return_pct": 22.60, "recent_month_return_pct": -3.45, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": -1.62, "benchmark_return_pct": -0.80, "peer_average_pct": -1.10, "rank": "19/27"},
            {"period": "近1月", "product_return_pct": -3.45, "benchmark_return_pct": -1.20, "peer_average_pct": -2.05, "rank": "21/27"},
            {"period": "近3月", "product_return_pct": 9.80, "benchmark_return_pct": 4.50, "peer_average_pct": 6.10, "rank": "6/27"},
            {"period": "近6月", "product_return_pct": 4.15, "benchmark_return_pct": 2.30, "peer_average_pct": 3.40, "rank": "10/26"},
            {"period": "近1年", "product_return_pct": 22.60, "benchmark_return_pct": 9.40, "peer_average_pct": 14.20, "rank": "5/25"},
            {"period": "成立以来", "product_return_pct": 48.70, "benchmark_return_pct": 18.90, "peer_average_pct": 30.50, "rank": "3/20"},
        ],
        "analysis_metrics": [
            {"label": "多策略", "score": 4, "value": "股票/商品/期货", "description": "全球多资产、多策略组合"},
            {"label": "杠杆与衍生品", "score": 1, "value": "最高 1.4 倍", "description": "收益和亏损都会被放大"},
            {"label": "回撤幅度", "score": 1, "value": "21.50%", "description": "可能短期大幅亏损"},
            {"label": "风险收益比", "score": 3, "value": "夏普 0.88", "description": "仅适合能承受高风险的投资者"},
        ],
        "manager": {
            "name": "温行之", "title": "量化与另类投资部 首席投资官", "tenure_since": "2019-09-02",
            "experience_years": 18.0, "managed_scale_billion": 5.6, "annualized_yield_pct": 13.20,
            "tenure_return_pct": 48.70, "avatar_url": "https://example.com/manager_wenxingzhi.png",
            "bio": "虚构人物。18 年海外多资产与衍生品投资经验，擅长趋势与套利策略",
        },
        "asset_allocation": [
            {"name": "全球股票", "percentage": 42.0},
            {"name": "商品与期货", "percentage": 28.0},
            {"name": "债券资产", "percentage": 18.0},
            {"name": "现金及保证金", "percentage": 12.0},
        ],
        "top_holdings": [
            {"name": "示例全球科技股篮子", "category": "海外股票", "percentage": 12.5, "code": "Z900101", "daily_change_pct": -1.86},
            {"name": "示例股指期货合约", "category": "股指期货", "percentage": 10.8, "code": "Z910202", "daily_change_pct": -2.40},
            {"name": "示例黄金期货合约", "category": "商品期货", "percentage": 9.2, "code": "Z920303", "daily_change_pct": 0.65},
            {"name": "示例原油期货合约", "category": "商品期货", "percentage": 7.6, "code": "Z920404", "daily_change_pct": -3.12},
            {"name": "示例美元短债", "category": "海外债券", "percentage": 6.9, "code": "Z930505", "daily_change_pct": 0.04},
        ],
        "established_at": "2019-09-02", "scale_billion": 2.1, "manager_company": _COMPANY,
        "custodian": "云岭银行股份有限公司", "fund_type": "另类投资类 · 多策略", "dividend_mode": "不分红",
        "original_fee_rate": 2.00, "discount_fee_rate": 0.20, "fee_rate": 1.50,
        "rating_agency": "演示评级 ★★★☆☆", "award_title": "演示奖项 · 多策略",
        "promo_title": "全球多策略 高风险高波动", "promo_subtitle": "仅限 C5 激进型投资者，可能出现较大亏损",
        **_INSTITUTION,
        "purchase_rule": "100000元起购，募集期内申请", "redemption_rule": "730天封闭期内不可赎回",
        "confirm_time": "T+2", "summary": "使用衍生品和杠杆的全球多策略产品，波动和亏损可能很大。",
        "announcement": _NOTICE,
    },
}

# 以月度快照生成覆盖近1月/近3月/近1年的归一化展示序列；仅用于读接口。
_WEALTH_NAV_BASE = ("2025-09-15", "2025-10-15", "2025-11-17", "2025-12-15", "2026-01-15", "2026-02-13", "2026-03-16", "2026-04-15", "2026-05-15", "2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15")


def _normalized_wealth_series(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a stable chart whose labeled period returns are mathematically true.

    This is a normalized performance chart, deliberately separate from the
    product's latest NAV.  Available documented periods are exact anchors;
    intervening monthly points are linear interpolation for presentation.  A
    fixture with an inception return also receives an explicit inception point,
    so selecting ``成立以来`` never relabels a one-year series as lifetime data.
    """

    history = {row["period"]: row for row in detail["performance_history"]}
    has_extended_history = {"近6月", "成立以来"} <= history.keys()
    total_period = "成立以来" if has_extended_history else "近1年"
    product_last = 1.0 + float(history[total_period]["product_return_pct"]) / 100.0
    benchmark_last = 1.0 + float(history[total_period]["benchmark_return_pct"]) / 100.0
    product_anchors = {
        0: product_last / (1.0 + float(history["近1年"]["product_return_pct"]) / 100.0),
        9: product_last / (1.0 + float(history["近3月"]["product_return_pct"]) / 100.0),
        11: product_last / (1.0 + float(history["近1月"]["product_return_pct"]) / 100.0),
        12: product_last,
    }
    benchmark_anchors = {
        0: benchmark_last / (1.0 + float(history["近1年"]["benchmark_return_pct"]) / 100.0),
        9: benchmark_last / (1.0 + float(history["近3月"]["benchmark_return_pct"]) / 100.0),
        11: benchmark_last / (1.0 + float(history["近1月"]["benchmark_return_pct"]) / 100.0),
        12: benchmark_last,
    }
    if has_extended_history:
        product_anchors[6] = product_last / (1.0 + float(history["近6月"]["product_return_pct"]) / 100.0)
        benchmark_anchors[6] = benchmark_last / (1.0 + float(history["近6月"]["benchmark_return_pct"]) / 100.0)
    series = []
    for index, date in enumerate(_WEALTH_NAV_BASE):
        left = max(anchor for anchor in product_anchors if anchor <= index)
        right = min(anchor for anchor in product_anchors if anchor >= index)
        ratio = 0.0 if left == right else (index - left) / (right - left)
        product_nav = product_anchors[left] + (product_anchors[right] - product_anchors[left]) * ratio
        benchmark_nav = benchmark_anchors[left] + (benchmark_anchors[right] - benchmark_anchors[left]) * ratio
        series.append({"date": date, "nav": round(product_nav, 6), "benchmark_nav": round(benchmark_nav, 6)})
    if has_extended_history and detail.get("established_at", "") < _WEALTH_NAV_BASE[0]:
        series.insert(0, {"date": detail["established_at"], "nav": 1.0, "benchmark_nav": 1.0})
    return series




def wealth_product_view(product: Mapping[str, Any]) -> dict[str, Any]:
    """Project presentation fixtures without persisting them with the ledger."""

    view = deepcopy(dict(product))
    detail = _WEALTH_DETAIL_FIXTURES.get(str(product.get("product_id")))
    if detail is None:
        return view
    view.update({"data_source": "MOCK_FIXTURE", "as_of_date": "2026-09-15", "nav_series": _normalized_wealth_series(detail), **deepcopy(detail)})
    return view
WEALTH_HOLDINGS: dict[str, list[dict[str, Any]]] = {"user_zhang": []}
# 风险档案与账户分开存储，初始为空：用户首次咨询理财业务时由 Agent 触发
# 8 题风险测评问卷（C1-C5），结果与原始答案一起记录在此（测评得分仅供
# 服务端定档，不对用户展示）。空档案让演示能完整走一遍"首次触发测评"。
WEALTH_RISK_PROFILES: dict[str, dict[str, Any]] = {}

# 风险测评重做限次（用户 -> 完成时间戳列表）：当日 ≤2 次、近 1 月 ≤4 次、
# 近 1 年 ≤8 次。滚动窗口按完成时刻计，随账本快照持久化。
RISK_ASSESSMENT_LIMITS: tuple[tuple[float, int, str], ...] = (
    (86400.0, 2, "今日"),
    (30 * 86400.0, 4, "近 1 个月"),
    (365 * 86400.0, 8, "近 1 年"),
)
RISK_ATTEMPTS: dict[str, list[float]] = {}
register_collections(WEALTH_PRODUCTS=WEALTH_PRODUCTS, WEALTH_HOLDINGS=WEALTH_HOLDINGS,
                     WEALTH_RISK_PROFILES=WEALTH_RISK_PROFILES, RISK_ATTEMPTS=RISK_ATTEMPTS)


def risk_assessment_attempts(user_id: str) -> dict[str, Any]:
    """某用户当前的重做余量（供触发侧在出卷前拒绝，而非交卷时才失败）。"""

    now = time.time()
    history = [ts for ts in RISK_ATTEMPTS.get(user_id, []) if now - ts < 365 * 86400.0]
    counts = {}
    for window, _limit, label in RISK_ASSESSMENT_LIMITS:
        counts[label] = sum(1 for ts in history if now - ts < window)
    allowed, message = True, ""
    for window, limit, label in RISK_ASSESSMENT_LIMITS:
        used = sum(1 for ts in history if now - ts < window)
        if used >= limit:
            allowed, message = False, f"风险测评重做次数已达上限：{label}最多 {limit} 次，请稍后再试"
            break
    return {"allowed": allowed, "message": message, "history": history, "counts": counts}


def record_risk_assessment(user_id: str) -> None:
    now = time.time()
    history = [ts for ts in RISK_ATTEMPTS.get(user_id, []) if now - ts < 365 * 86400.0]
    history.append(now)
    RISK_ATTEMPTS[user_id] = history

# 8 题问卷评分表（题目编号 -> 选项得分），服务端定档唯一权威。
RISK_QUIZ_SCORES: dict[int, dict[str, int]] = {
    1: {"A": 5, "B": 4, "C": 3, "D": 1},
    2: {"A": 1, "B": 3, "C": 4, "D": 5},
    3: {"A": 1, "B": 2, "C": 4, "D": 5},
    4: {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5},
    5: {"A": 1, "B": 2, "C": 3, "D": 5},
    6: {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5},
    7: {"A": 0, "B": 2, "C": 3, "D": 4, "E": 5},
    8: {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5},
}
# 总分 -> 投资者类型（类型名/特点/匹配产品级别）。得分不对用户展示。
RISK_GRADE_TABLE: tuple[tuple[int, str], ...] = ((36, "C5"), (30, "C4"), (23, "C3"), (15, "C2"), (8, "C1"))
RISK_GRADE_META: dict[str, tuple[str, str, str]] = {
    "C1": ("保守型", "风险厌恶度极高，首要目标为资产保全", "R1（低风险：现金管理类、存款）"),
    "C2": ("稳健型", "偏好本金相对安全，可承受极小净值波动", "R1、R2（中低风险：纯债类、稳健固收）"),
    "C3": ("平衡型", "追求收益与风险平衡，兼顾债底与权益增厚", "R1 ~ R3（中等风险：固收+、平衡混合）"),
    "C4": ("进取型", "具备较强抗风险能力，主张长期资本增值", "R1 ~ R4（中高风险：权益类、股票型基金）"),
    "C5": ("激进型", "追求高收益，能承受本金重大损失", "R1 ~ R5（高风险：高杠杆、衍生品、行业主题）"),
}


PROFILE_LABELS: dict[str, str] = {
    "CONSERVATIVE": "保守型", "C1": "保守型",
    "C2": "稳健型", "BALANCED": "平衡型", "C3": "平衡型",
    "C4": "进取型", "AGGRESSIVE": "进取型",
    "C5": "激进型",
}


def risk_grade_for_score(score: int) -> str:
    for threshold, grade in RISK_GRADE_TABLE:
        if score >= threshold:
            return grade
    return "C1"


def profile_rank(profile: Optional[str]) -> Optional[int]:
    # 档案代码 -> 可购买的最高产品风险等级（R 几）。
    return {
        "CONSERVATIVE": 1, "C1": 1,
        "BALANCED": 2, "C2": 2,
        "C3": 3,
        "AGGRESSIVE": 4, "C4": 4,
        "C5": 5,
    }.get(str(profile or "").upper())
