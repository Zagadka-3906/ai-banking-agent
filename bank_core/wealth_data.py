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
# 演示产品货架：四款虚构产品覆盖 R1-R4 完整风险谱系。除旧字段外新增
# category/benchmark/term/underlying/target_clients，供理财页展示完整要素；
# 全部为虚构演示数据，业绩比较基准不是收益承诺。
WEALTH_PRODUCTS: list[dict[str, Any]] = [
    {
        "product_id": "CJ01002",
        "name": "稳健添利·日日盈",
        "category": "现金管理类",
        "risk_level": "R1",
        "benchmark": "2.05% ~ 2.35%（7日年化）",
        "annualized_yield": 0.022,
        "min_amount": 1.0,
        "liquidity": "T+0 快速赎回/T+1 确认",
        "term": "灵活申赎（工作日 T+1 确认，单日快速赎回最高 1 万元）",
        "underlying": "高流动性资产：银行存款、同业存单、高信用评级短期债券",
        "target_clients": "追求资金高流动性、主要存放日常零花与备用金的保守型投资者",
    },
    {
        "product_id": "AY90204",
        "name": "安逸安享·季季丰90天",
        "category": "固定收益类",
        "risk_level": "R2",
        "benchmark": "2.75% ~ 3.10%（年化）",
        "annualized_yield": 0.029,
        "min_amount": 1000.0,
        "liquidity": "90 天封闭",
        "term": "90 天封闭运作，到期自动滚存或转出",
        "underlying": "80% 以上配置国债、政策性金融债、AAA 级企业信用债，其余投资货币市场工具",
        "target_clients": "有短期闲置资金，希望收益高于活期存款且能接受固定锁定期限的用户",
    },
    {
        "product_id": "ZY18031",
        "name": "智远创赢·成长平衡180天",
        "category": "固收+混合类",
        "risk_level": "R3",
        "benchmark": "3.60% ~ 4.80%（年化）",
        "annualized_yield": 0.042,
        "min_amount": 10000.0,
        "liquidity": "180 天定开",
        "term": "180天运作期",
        "underlying": "主仓位（70%~85%）投资中高等级固定收益证券筑底；副仓位（15%~30%）参与沪深300成分股打新、高股息红利资产及量化中性策略",
        "target_clients": "具有一定风险承受能力，希望在债底防守基础上争取权益市场超额收益的稳健进取型客户",
    },
    {
        "product_id": "QK36509",
        "name": "乾坤进取·产业智选封闭1年",
        "category": "权益类",
        "risk_level": "R4",
        "benchmark": "沪深300指数收益率×70% + 中债综合财富指数收益率×30%",
        "annualized_yield": None,
        "min_amount": 50000.0,
        "liquidity": "365 天封闭",
        "term": "365 天全封闭运行",
        "underlying": "不低于 80% 资产直接或间接投资于 A 股及港股通标的，重点布局半导体、先进制造与高端装备等成长行业",
        "target_clients": "能够承受本金波动、看好产业转型长期红利的高净值积极型投资者",
    },
]

# 详情页使用的静态行情快照。数值均为虚构演示数据，明确标注快照日期，
# 不代表实时行情、历史实际表现或未来收益，也不参与任何交易决策与记账。
_WEALTH_DETAIL_FIXTURES: dict[str, dict[str, Any]] = {
    "CJ01002": {
        "latest_nav": 1.0261, "daily_change_pct": 0.02,
        "annual_return_pct": 2.61, "recent_month_return_pct": 0.18, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 0.05, "benchmark_return_pct": 0.04, "peer_average_pct": 0.04, "rank": "18/86"},
            {"period": "近1月", "product_return_pct": 0.18, "benchmark_return_pct": 0.17, "peer_average_pct": 0.16, "rank": "32/86"},
            {"period": "近3月", "product_return_pct": 0.61, "benchmark_return_pct": 0.56, "peer_average_pct": 0.52, "rank": "28/86"},
            {"period": "近6月", "product_return_pct": 1.28, "benchmark_return_pct": 1.15, "peer_average_pct": 1.10, "rank": "20/86"},
            {"period": "近1年", "product_return_pct": 2.61, "benchmark_return_pct": 2.35, "peer_average_pct": 2.25, "rank": "24/86"},
            {"period": "成立以来", "product_return_pct": 8.92, "benchmark_return_pct": 7.80, "peer_average_pct": 7.60, "rank": "15/80"},
        ],
        "analysis_metrics": [
            {"label": "超低波动风格", "score": 5, "value": "波动率 0.05%", "description": "净值平稳几乎无回撤"},
            {"label": "现金管理定位", "score": 5, "value": "活期替代", "description": "日常零花与备用金优选"},
            {"label": "高流动性支持", "score": 5, "value": "T+0 极速", "description": "支持单日快速赎回 1 万元"},
            {"label": "夏普比率 3.28", "score": 5, "value": "回撤 0.00%", "description": "风险调整后性价比极高"},
        ],
        "manager": {
            "name": "林晨", "title": "现金管理投资部 董事总经理", "tenure_since": "2021-03-04",
            "experience_years": 9.0, "managed_scale_billion": 85.2, "annualized_yield_pct": 2.75,
            "tenure_return_pct": 8.92, "avatar_url": "https://example.com/manager_linchen.png",
            "bio": "复旦大学金融工程硕士，专注现金管理与短久期流动性资产配置，风控严谨稳健",
        },
        "asset_allocation": [
            {"name": "银行存款", "percentage": 45.0},
            {"name": "同业存单", "percentage": 30.0},
            {"name": "短期债券", "percentage": 20.0},
            {"name": "现金及其他", "percentage": 5.0},
        ],
        "top_holdings": [
            {"name": "24中国农业银行存单088", "category": "同业存单", "percentage": 14.5, "code": "01240088", "daily_change_pct": 0.01},
            {"name": "24中国工商银行CD052", "category": "同业存单", "percentage": 12.8, "code": "01240052", "daily_change_pct": 0.01},
            {"name": "24建设银行同业存单", "category": "同业存单", "percentage": 11.2, "code": "01240091", "daily_change_pct": 0.00},
            {"name": "23国开行金融债01", "category": "政策金融债", "percentage": 8.5, "code": "230201", "daily_change_pct": 0.02},
            {"name": "24中行同业存单012", "category": "同业存单", "percentage": 7.6, "code": "01240012", "daily_change_pct": 0.01},
        ],
        "established_at": "2021-03-04", "scale_billion": 63.8, "manager_company": "华夏量化理财管理有限公司",
        "custodian": "中国工商银行股份有限公司", "fund_type": "现金管理类 · 货币型", "dividend_mode": "每日结转 / 红利再投资",
        "original_fee_rate": 0.50, "discount_fee_rate": 0.00, "fee_rate": 0.00,
        "rating_agency": "晨星评级 ★★★★★", "award_title": "金选现金管理奖 · 卓越流动性",
        "promo_title": "零钱理财 每日计息", "promo_subtitle": "申购费率0折，快速赎回实时到账",
        "institution_founded_years": 26, "institution_aum_billion": 21380.93, "institution_rating": "综合评级 AAA",
        "purchase_rule": "1元起购，工作日 00:00-15:00 申请", "redemption_rule": "工作日支持快速赎回，单日上限1万元",
        "confirm_time": "T+1", "summary": "适合日常备用金管理的超低波动现金管理产品，每日计算收益并结转份额。",
        "announcement": "本页面为 Mock 行情快照，仅供产品演示，不构成投资建议。",
    },
    "AY90204": {
        "latest_nav": 1.0738, "daily_change_pct": 0.08,
        "annual_return_pct": 3.05, "recent_month_return_pct": 0.24, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 0.06, "benchmark_return_pct": 0.05, "peer_average_pct": 0.05, "rank": "15/74"},
            {"period": "近1月", "product_return_pct": 0.24, "benchmark_return_pct": 0.23, "peer_average_pct": 0.21, "rank": "18/74"},
            {"period": "近3月", "product_return_pct": 0.76, "benchmark_return_pct": 0.72, "peer_average_pct": 0.68, "rank": "21/74"},
            {"period": "近6月", "product_return_pct": 1.52, "benchmark_return_pct": 1.45, "peer_average_pct": 1.38, "rank": "17/74"},
            {"period": "近1年", "product_return_pct": 3.05, "benchmark_return_pct": 2.92, "peer_average_pct": 2.78, "rank": "16/74"},
            {"period": "成立以来", "product_return_pct": 18.25, "benchmark_return_pct": 16.50, "peer_average_pct": 15.80, "rank": "12/65"},
        ],
        "analysis_metrics": [
            {"label": "纯债筑底风格", "score": 4, "value": "中短久期", "description": "精选AAA信用债与利率债"},
            {"label": "最大回撤控制", "score": 5, "value": "0.18%", "description": "回撤控制能力卓越，平稳向上"},
            {"label": "机构重仓比例", "score": 4, "value": "82.5%", "description": "险资与机构客户重仓青睐"},
            {"label": "夏普比率 2.85", "score": 4, "value": "波动率 0.42%", "description": "稳健收益性价比高，持有体验佳"},
        ],
        "manager": {
            "name": "周宁", "title": "固定收益投资部 执行总经理", "tenure_since": "2020-07-15",
            "experience_years": 12.0, "managed_scale_billion": 45.8, "annualized_yield_pct": 3.42,
            "tenure_return_pct": 18.25, "avatar_url": "https://example.com/manager_zhouneng.png",
            "bio": "上海财经大学硕士，12年债券投研经验，擅长利率走势预判与信用利差挖掘",
        },
        "asset_allocation": [
            {"name": "利率债", "percentage": 40.0},
            {"name": "信用债", "percentage": 40.0},
            {"name": "货币工具", "percentage": 15.0},
            {"name": "现金及其他", "percentage": 5.0},
        ],
        "top_holdings": [
            {"name": "23附息国债12", "category": "记账式国债", "percentage": 14.5, "code": "019712", "daily_change_pct": 0.03},
            {"name": "22进出口行05", "category": "政策金融债", "percentage": 12.0, "code": "220305", "daily_change_pct": 0.04},
            {"name": "23中石油MTN002", "category": "AAA级中票", "percentage": 9.8, "code": "10238012", "daily_change_pct": 0.05},
            {"name": "23国家电网债01", "category": "央企信用债", "percentage": 8.2, "code": "10238055", "daily_change_pct": 0.02},
            {"name": "24国开05", "category": "政策金融债", "percentage": 6.5, "code": "240205", "daily_change_pct": 0.04},
        ],
        "established_at": "2020-07-15", "scale_billion": 42.6, "manager_company": "华夏量化理财管理有限公司",
        "custodian": "中国农业银行股份有限公司", "fund_type": "固定收益类 · 纯债型", "dividend_mode": "季季分红 / 现金分红",
        "original_fee_rate": 0.80, "discount_fee_rate": 0.08, "fee_rate": 0.15,
        "rating_agency": "晨星评级 ★★★★★", "award_title": "金牛固收产品奖 · 稳健回报标杆",
        "promo_title": "季季分红 稳健优选", "promo_subtitle": "申购费率1折，持有到期免赎回费",
        "institution_founded_years": 26, "institution_aum_billion": 21380.93, "institution_rating": "综合评级 AAA",
        "purchase_rule": "1000元起购，工作日 00:00-15:00 申请", "redemption_rule": "封闭期满后可申请赎回或自动滚存",
        "confirm_time": "T+1", "summary": "以优质高等级固收资产打底、期限明确的稳健型纯债理财产品。",
        "announcement": "本页面为 Mock 行情快照，仅供产品演示，不构成投资建议。",
    },
    "ZY18031": {
        "latest_nav": 1.2761, "daily_change_pct": 0.38,
        "annual_return_pct": 19.46, "recent_month_return_pct": 3.89, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1月", "product_return_pct": 3.89, "peer_average_pct": 1.18, "benchmark_return_pct": 1.18, "rank": "58/420"},
            {"period": "近3月", "product_return_pct": 8.67, "peer_average_pct": 3.42, "benchmark_return_pct": 3.42, "rank": "40/415"},
            {"period": "近6月", "product_return_pct": -2.12, "peer_average_pct": -4.25, "benchmark_return_pct": -4.25, "rank": "51/390"},
            {"period": "近1年", "product_return_pct": 19.46, "peer_average_pct": 10.28, "benchmark_return_pct": 10.28, "rank": "49/360"},
            {"period": "成立以来", "product_return_pct": 36.95, "peer_average_pct": 19.50, "benchmark_return_pct": 19.50, "rank": "36/280"},
        ],
        "analysis_metrics": [
            {"label": "大盘成长风格", "score": 4, "value": "成长均衡", "description": "聚焦核心资产与行业龙头"},
            {"label": "行业配置均衡", "score": 4, "value": "制造/消费", "description": "兼顾防守底仓与进攻弹性"},
            {"label": "机构重仓比例", "score": 4, "value": "78.2%", "description": "专业机构投资者高度认可"},
            {"label": "夏普比率 1.45", "score": 4, "value": "回撤 3.12%", "description": "控制力优秀 · 稳健增厚"},
        ],
        "manager": {
            "name": "陈敏", "title": "固定收益与多资产部 投资总监", "tenure_since": "2015-06-01",
            "experience_years": 11.0, "managed_scale_billion": 32.6, "annualized_yield_pct": 12.88,
            "tenure_return_pct": 57.51, "avatar_url": "https://example.com/manager_avatar.png",
            "bio": "清华大学金融硕士，从业11年又94天，擅长固收增强与多资产配置，长期业绩优异",
        },
        "asset_allocation": [
            {"name": "债券资产", "percentage": 74.5},
            {"name": "股票权益", "percentage": 18.2},
            {"name": "现金及其他", "percentage": 7.3},
        ],
        "promo_title": "财富日限时加息", "promo_subtitle": "申购费率1折，额度有限",
        "institution_founded_years": 26, "institution_aum_billion": 21380.93, "institution_rating": "综合评级 AA+",
        "custodian": "中国建设银行股份有限公司", "fund_type": "固收+混合类 · 中风险", "dividend_mode": "现金分红 / 红利再投资",
        "original_fee_rate": 1.00, "discount_fee_rate": 0.10,
        "rating_agency": "晨星评级 ★★★★★", "award_title": "金牛基金奖 · 卓越回报标杆",
        "top_holdings": [
            {"name": "中债优选180天资产支持专项计划", "category": "债券资产", "percentage": 18.6, "code": "190288", "daily_change_pct": 0.05},
            {"name": "国开行金融债2026年第七期", "category": "利率债", "percentage": 15.2, "code": "260207", "daily_change_pct": 0.08},
            {"name": "华域汽车", "category": "制造业权益", "percentage": 4.8, "code": "600741", "daily_change_pct": 1.35},
            {"name": "宁德时代", "category": "先进制造权益", "percentage": 3.9, "code": "300750", "daily_change_pct": 2.15},
            {"name": "中证红利量化增强策略", "category": "量化策略", "percentage": 3.4, "code": "501029", "daily_change_pct": -0.42},
        ],
        "established_at": "2000-09-01", "scale_billion": 63.8, "manager_company": "华夏量化理财管理有限公司",
        "purchase_rule": "10000元起购，开放期内申请", "redemption_rule": "每180天开放一次赎回",
        "confirm_time": "T+1", "fee_rate": 0.60,
        "summary": "在固收底仓基础上争取权益增强收益的平衡型产品，兼顾回撤防守与超额增长。",
        "announcement": "本页面为 Mock 行情快照，仅供产品演示，不构成投资建议。",
    },
    "QK36509": {
        "latest_nav": 1.2643, "daily_change_pct": 0.41,
        "annual_return_pct": 12.64, "recent_month_return_pct": 1.32, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 0.85, "benchmark_return_pct": 0.62, "peer_average_pct": 0.68, "rank": "11/43"},
            {"period": "近1月", "product_return_pct": 1.32, "benchmark_return_pct": 1.08, "peer_average_pct": 1.15, "rank": "12/43"},
            {"period": "近3月", "product_return_pct": 4.86, "benchmark_return_pct": 4.12, "peer_average_pct": 4.30, "rank": "10/43"},
            {"period": "近6月", "product_return_pct": 6.85, "benchmark_return_pct": 5.20, "peer_average_pct": 5.60, "rank": "9/43"},
            {"period": "近1年", "product_return_pct": 12.64, "benchmark_return_pct": 10.21, "peer_average_pct": 10.85, "rank": "8/43"},
            {"period": "成立以来", "product_return_pct": 48.60, "benchmark_return_pct": 35.20, "peer_average_pct": 38.40, "rank": "6/38"},
        ],
        "analysis_metrics": [
            {"label": "硬科技成长风格", "score": 4, "value": "先进制造", "description": "专注半导体、新材料与高端装备"},
            {"label": "高弹性进攻定位", "score": 4, "value": "超额显著", "description": "结构性行情具备强超额能力"},
            {"label": "机构重仓比例", "score": 4, "value": "76.4%", "description": "头部公募与机构重点配置"},
            {"label": "夏普比率 1.28", "score": 3, "value": "回撤 8.45%", "description": "中高风险高收益回报匹配"},
        ],
        "manager": {
            "name": "沈岳", "title": "权益投资部 董事总经理", "tenure_since": "2018-06-12",
            "experience_years": 17.0, "managed_scale_billion": 18.5, "annualized_yield_pct": 15.60,
            "tenure_return_pct": 48.60, "avatar_url": "https://example.com/manager_shenyue.png",
            "bio": "清华大学物理学学士、清华五道口金融硕士，17年投研经验，专注硬科技产业升级",
        },
        "asset_allocation": [
            {"name": "权益资产", "percentage": 70.0},
            {"name": "债券资产", "percentage": 20.0},
            {"name": "现金及其他", "percentage": 10.0},
        ],
        "top_holdings": [
            {"name": "宁德时代", "category": "新能源制造", "percentage": 8.9, "code": "300750", "daily_change_pct": 2.15},
            {"name": "中芯国际", "category": "集成电路", "percentage": 8.2, "code": "688981", "daily_change_pct": 3.42},
            {"name": "比亚迪", "category": "整车与电池", "percentage": 7.8, "code": "002594", "daily_change_pct": 1.18},
            {"name": "汇川技术", "category": "工控自动化", "percentage": 6.9, "code": "300124", "daily_change_pct": 0.85},
            {"name": "阳光电源", "category": "光伏逆变器", "percentage": 6.2, "code": "300274", "daily_change_pct": 1.95},
        ],
        "established_at": "2018-06-12", "scale_billion": 9.7, "manager_company": "华夏量化理财管理有限公司",
        "custodian": "招商银行股份有限公司", "fund_type": "权益类 · 行业主题型", "dividend_mode": "现金分红 / 红利再投资",
        "original_fee_rate": 1.50, "discount_fee_rate": 0.15, "fee_rate": 1.20,
        "rating_agency": "晨星评级 ★★★★★", "award_title": "硬科技主题卓越基金 · 金选标杆",
        "promo_title": "产业智选 领跑成长", "promo_subtitle": "申购费率1折，聚焦先进制造与硬科技",
        "institution_founded_years": 26, "institution_aum_billion": 21380.93, "institution_rating": "综合评级 AAA",
        "purchase_rule": "50000元起购，募集期内申请", "redemption_rule": "封闭期满后开放赎回",
        "confirm_time": "T+1", "summary": "面向长期资金、聚焦产业成长机会的高波动权益类产品。",
        "announcement": "本页面为 Mock 行情快照，仅供产品演示，不构成投资建议。",
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




def _wealth_product_view(product: Mapping[str, Any]) -> dict[str, Any]:
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
