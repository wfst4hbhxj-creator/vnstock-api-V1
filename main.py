from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import vnstock
import logging
import time
from datetime import date, timedelta, datetime
from functools import lru_cache

# =====================================
# VNSTOCK v4 — Unified UI
# =====================================
from vnstock import Market, Reference, Fundamental

app = FastAPI(title="VNStock API", version="4.1")

# =====================================
# LOGGING
# =====================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("vnstock-api")

# =====================================
# MODELS
# =====================================
class PortfolioRequest(BaseModel):
    stocks: list[str]

# =====================================
# CONFIG
# =====================================
WATCHED_FUNDS = ["DCDS", "DCDE", "DCBF"]

# =====================================
# TTL CACHE đơn giản (không cần Redis)
# fund holdings = 6h, company/financial = 24h, news = 1h
# =====================================
_cache: dict = {}

def _cache_get(key: str):
    item = _cache.get(key)
    if item and time.time() < item["expires"]:
        return item["data"]
    return None

def _cache_set(key: str, data, ttl_seconds: int):
    _cache[key] = {"data": data, "expires": time.time() + ttl_seconds}

TTL_FUND    = 6  * 3600
TTL_COMPANY = 24 * 3600
TTL_FINANCE = 24 * 3600
TTL_NEWS    =  1 * 3600

# =====================================
# HELPERS
# =====================================
def _safe_float(val):
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None

def _today() -> str:
    return date.today().strftime("%Y-%m-%d")

def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")

def _err(detail: str, status: int = 500):
    """Trả về JSON lỗi chuẩn thay vì 500."""
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": detail}
    )

def _fetch_fund_holdings(fund_name: str):
    """Lấy top holdings của quỹ, cache 6h."""
    key = f"fund_holdings_{fund_name}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        data = Reference().fund(fund_name).top_holding()
    except Exception:
        data = Market().fund(fund_name).top_holding()
    _cache_set(key, data, TTL_FUND)
    return data

def _col(df, *names):
    """Tìm tên cột linh hoạt (case-insensitive)."""
    for name in names:
        if name in df.columns:
            return name
        for col in df.columns:
            if str(col).lower() == name.lower():
                return col
    return None

# =====================================
# META
# =====================================
@app.get("/")
def home():
    return {"status": "ok", "service": "vnstock-api", "version": "4.1", "vnstock": vnstock.__version__}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.get("/version")
def version():
    return {"api_version": "4.1", "vnstock_version": vnstock.__version__}

@app.get("/info")
def info():
    return {
        "service": "vnstock-api", "version": "4.1",
        "vnstock_version": vnstock.__version__,
        "watched_funds": WATCHED_FUNDS,
        "endpoints": [
            "GET  /stock/{symbol}", "GET  /company/{symbol}",
            "GET  /dividend/{symbol}", "GET  /financial-summary/{symbol}",
            "GET  /etf/{symbol}",
            "GET  /fund/{symbol}", "GET  /fund/{symbol}/top", "GET  /fund/{symbol}/industry",
            "GET  /fund-check/{symbol}", "GET  /fund-favorites",
            "GET  /score/{symbol}", "GET  /quality/{symbol}",
            "GET  /hold/{symbol}", "GET  /compare/{symbol1}/{symbol2}",
            "GET  /recommend", "POST /portfolio-score",
            "GET  /market",
            "GET  /news/{symbol}",
            "GET  /analyze/{symbol}",
            "GET  /index/{symbol}",
            "GET  /growth-stocks",
            "GET  /dividend-kings",
        ]
    }

# =====================================
# GIÁ CỔ PHIẾU MỚI NHẤT
# =====================================
@app.get("/stock/{symbol}")
def get_stock_price(symbol: str):
    symbol = symbol.upper()
    try:
        mkt = Market()
        quote = mkt.equity(symbol).ohlcv(start=_days_ago(5), end=_today(), interval="1D")
        if quote is None or quote.empty:
            return _err(f"Không có dữ liệu giá cho {symbol}", 404)
        latest = quote.iloc[-1]
        close_col = _col(quote, "close", "Close") or quote.columns[-2]
        vol_col   = _col(quote, "volume", "Volume") or quote.columns[-1]
        return {
            "symbol": symbol,
            "close": float(latest[close_col]),
            "volume": int(latest[vol_col])
        }
    except Exception as e:
        logger.error(f"/stock/{symbol}: {e}")
        return _err(str(e))

# =====================================
# THÔNG TIN CÔNG TY (cache 24h)
# =====================================
@app.get("/company/{symbol}")
def get_company(symbol: str):
    symbol = symbol.upper()
    key = f"company_{symbol}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        data = Reference().company(symbol).info()
        if data is None or (hasattr(data, "empty") and data.empty):
            return _err(f"Không tìm thấy công ty {symbol}", 404)
        result = data.to_dict(orient="records") if hasattr(data, "to_dict") else data
        _cache_set(key, result, TTL_COMPANY)
        return result
    except Exception as e:
        logger.error(f"/company/{symbol}: {e}")
        return _err(str(e))

# =====================================
# CỔ TỨC
# =====================================
@app.get("/dividend/{symbol}")
def get_dividend(symbol: str):
    symbol = symbol.upper()
    key = f"dividend_{symbol}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        ref = Reference()
        # v4: thử events() lọc cổ tức
        try:
            events = ref.company(symbol).events()
            if events is not None and not events.empty:
                div_mask = events.apply(
                    lambda r: any(kw in str(r).lower() for kw in ["cổ tức","dividend","chi tra","cash"]),
                    axis=1
                )
                divs = events[div_mask]
                if not divs.empty:
                    result = divs.to_dict(orient="records")
                    _cache_set(key, result, TTL_COMPANY)
                    return result
        except Exception:
            pass
        # fallback Finance ratio dividend yield
        try:
            from vnstock import Finance
            fin = Finance(symbol=symbol, source="KBS")
            ratio = fin.ratio(period="year", lang="en")
            if ratio is not None and not ratio.empty:
                div_cols = [c for c in ratio.columns if "dividend" in str(c).lower() or "div" in str(c).lower()]
                if div_cols:
                    all_cols = (["period"] if "period" in ratio.columns else []) + div_cols
                    result = ratio[all_cols].head(8).to_dict(orient="records")
                    _cache_set(key, result, TTL_COMPANY)
                    return result
        except Exception:
            pass
        return _err(f"Không có dữ liệu cổ tức cho {symbol}", 404)
    except Exception as e:
        logger.error(f"/dividend/{symbol}: {e}")
        return _err(str(e))

# =====================================
# CHỈ SỐ TÀI CHÍNH (cache 24h)
# =====================================
@app.get("/financial-summary/{symbol}")
def get_financial_summary(symbol: str):
    symbol = symbol.upper()
    key = f"financial_{symbol}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        ratios = Fundamental().equity(symbol).ratios()
        if ratios is None or ratios.empty:
            return _err(f"Không có dữ liệu tài chính cho {symbol}", 404)
        latest = ratios.iloc[0]

        def get_val(*keys):
            for k in keys:
                if k in latest.index:
                    return _safe_float(latest[k])
                for col in latest.index:
                    if k.lower() == str(col).lower():
                        return _safe_float(latest[col])
            return None

        result = {
            "symbol": symbol,
            "periods": len(ratios),
            "latest": {
                "roe":            get_val("roe","ROE","return_on_equity"),
                "roa":            get_val("roa","ROA","return_on_assets"),
                "eps":            get_val("eps","EPS","earnings_per_share"),
                "pe":             get_val("pe","PE","price_to_earnings","p_e"),
                "pb":             get_val("pb","PB","price_to_book","p_b"),
                "debt_to_equity": get_val("debt_to_equity","D/E","de_ratio"),
            },
            "history": ratios.head(5).to_dict(orient="records")
        }
        _cache_set(key, result, TTL_FINANCE)
        return result
    except Exception as e:
        logger.error(f"/financial-summary/{symbol}: {e}")
        return _err(str(e))

# =====================================
# ETF
# =====================================
@app.get("/etf/{symbol}")
def get_etf(symbol: str):
    symbol = symbol.upper()
    try:
        data = Market().etf(symbol).ohlcv(start=_days_ago(30), end=_today())
        if data is None or data.empty:
            return _err(f"Không có dữ liệu ETF {symbol}", 404)
        return data.tail(10).to_dict(orient="records")
    except Exception as e:
        return _err(str(e))

# =====================================
# QUỸ MỞ
# =====================================
@app.get("/fund/{symbol}")
def get_fund_nav(symbol: str):
    symbol = symbol.upper()
    try:
        mkt = Market()
        try:
            nav = mkt.fund(symbol).nav()
        except Exception:
            nav = mkt.fund(symbol).history()
        if nav is None or nav.empty:
            return _err(f"Không có dữ liệu NAV cho quỹ {symbol}", 404)
        return nav.tail(20).to_dict(orient="records")
    except Exception as e:
        return _err(str(e))

@app.get("/fund/{symbol}/top")
def get_fund_top_holdings(symbol: str):
    symbol = symbol.upper()
    try:
        data = _fetch_fund_holdings(symbol)
        if data is None or data.empty:
            return _err(f"Không có top holdings cho quỹ {symbol}", 404)
        return data.to_dict(orient="records")
    except Exception as e:
        return _err(str(e))

@app.get("/fund/{symbol}/industry")
def get_fund_industry(symbol: str):
    symbol = symbol.upper()
    try:
        try:
            data = Reference().fund(symbol).industry_holding()
        except Exception:
            data = Market().fund(symbol).industry_holding()
        if data is None or data.empty:
            return _err(f"Không có dữ liệu phân bổ ngành cho quỹ {symbol}", 404)
        return data.to_dict(orient="records")
    except Exception as e:
        return _err(str(e))

# =====================================
# FUND FAVORITES
# =====================================
@app.get("/fund-favorites")
def get_fund_favorites():
    result = {}
    for fund_name in ["DCDS", "DCDE"]:
        try:
            holdings = _fetch_fund_holdings(fund_name)
            result[fund_name] = holdings.head(10).to_dict(orient="records")
        except Exception:
            result[fund_name] = []
    return result

# =====================================
# KIỂM TRA QUỸ NẮM GIỮ
# =====================================
@app.get("/fund-check/{symbol}")
def get_fund_check(symbol: str):
    symbol = symbol.upper()
    held_by = []
    for fund_name in WATCHED_FUNDS:
        try:
            holdings = _fetch_fund_holdings(fund_name)
            code_col   = _col(holdings, "stock_code", "symbol", "ticker")
            weight_col = _col(holdings, "net_asset_percent", "weight", "allocation")
            if code_col and symbol in holdings[code_col].values:
                row = holdings[holdings[code_col] == symbol].iloc[0]
                weight = _safe_float(row[weight_col]) if weight_col else 0.0
                held_by.append({"fund": fund_name, "weight": weight or 0.0})
        except Exception:
            pass
    return {"symbol": symbol, "held_by": held_by, "fund_count": len(held_by)}

# =====================================
# CHẤM ĐIỂM CỔ PHIẾU — thang 100 điểm
#
# Quỹ nắm giữ  : tối đa 20đ (10đ/quỹ, tối đa 2 quỹ)
# Cổ tức        : 15đ
# DN data       : 5đ
# Tài chính     : 5đ (có data)
# ROE >= 20%    : 15đ | ROE >= 15%: 10đ | ROE >= 10%: 5đ
# ROA >= 10%    : 10đ | ROA >= 5%: 5đ
# EPS > 0       : 5đ
# D/E < 0.5     : 10đ | D/E < 1: 5đ
# PE < 15       : 5đ  | PE < 25: 3đ
# PB < 2        : 5đ  | PB < 3: 3đ
# Tổng tối đa   : 100đ
# =====================================
@app.get("/score/{symbol}")
def get_score(symbol: str):
    symbol = symbol.upper()
    logger.info(f"/score/{symbol} — bắt đầu chấm điểm")
    total_score = 0
    reasons = []

    # Quỹ nắm giữ (20đ)
    try:
        fund_data = get_fund_check(symbol)
        held_by = fund_data.get("held_by", [])
        if held_by:
            fund_score = min(len(held_by) * 10, 20)
            total_score += fund_score
            for f in held_by:
                reasons.append(f"{f['fund']} nắm giữ {f['weight']:.2f}% NAV")
    except Exception:
        pass

    # Dữ liệu doanh nghiệp (5đ)
    try:
        co = get_company(symbol)
        if isinstance(co, list) and len(co) > 0:
            total_score += 5
            reasons.append("Có dữ liệu doanh nghiệp")
    except Exception:
        pass

    # Cổ tức (15đ)
    try:
        div = get_dividend(symbol)
        if isinstance(div, list) and len(div) > 0:
            total_score += 15
            reasons.append(f"Có {len(div)} kỳ cổ tức/sự kiện")
    except Exception:
        pass

    # Tài chính & chỉ số (tổng tối đa 60đ)
    try:
        fin = get_financial_summary(symbol)
        if isinstance(fin, dict) and fin.get("periods", 0) > 0:
            total_score += 5
            reasons.append("Có dữ liệu tài chính")
            l = fin.get("latest", {})

            roe = l.get("roe")
            if roe is not None:
                if roe >= 20:   total_score += 15; reasons.append(f"ROE xuất sắc ({roe:.1f}%)")
                elif roe >= 15: total_score += 10; reasons.append(f"ROE tốt ({roe:.1f}%)")
                elif roe >= 10: total_score += 5;  reasons.append(f"ROE khá ({roe:.1f}%)")

            roa = l.get("roa")
            if roa is not None:
                if roa >= 10:  total_score += 10; reasons.append(f"ROA cao ({roa:.1f}%)")
                elif roa >= 5: total_score += 5;  reasons.append(f"ROA khá ({roa:.1f}%)")

            eps = l.get("eps")
            if eps is not None and eps > 0:
                total_score += 5
                reasons.append(f"EPS dương ({eps:,.0f})")

            debt = l.get("debt_to_equity")
            if debt is not None:
                if debt < 0.5:  total_score += 10; reasons.append(f"Nợ rất thấp D/E={debt:.2f}")
                elif debt < 1:  total_score += 5;  reasons.append(f"Nợ thấp D/E={debt:.2f}")

            pe = l.get("pe")
            if pe is not None and pe > 0:
                if pe < 15:  total_score += 5; reasons.append(f"P/E hấp dẫn ({pe:.1f})")
                elif pe < 25: total_score += 3; reasons.append(f"P/E hợp lý ({pe:.1f})")

            pb = l.get("pb")
            if pb is not None and pb > 0:
                if pb < 2:  total_score += 5; reasons.append(f"P/B hấp dẫn ({pb:.1f})")
                elif pb < 3: total_score += 3; reasons.append(f"P/B hợp lý ({pb:.1f})")
    except Exception:
        pass

    total_score = min(total_score, 100)

    if total_score >= 90:   rating = "Xuất sắc"
    elif total_score >= 75: rating = "Rất tốt"
    elif total_score >= 60: rating = "Tốt"
    elif total_score >= 40: rating = "Theo dõi"
    else:                    rating = "Yếu"

    logger.info(f"/score/{symbol} — {total_score}/100 ({rating})")
    return {"symbol": symbol, "score": total_score, "rating": rating, "reasons": reasons}

# =====================================
# CHẤT LƯỢNG & KHUYẾN NGHỊ
# =====================================
@app.get("/quality/{symbol}")
def get_quality(symbol: str):
    symbol = symbol.upper()
    data = get_score(symbol)
    score_value = data.get("score", 0)
    rating = data.get("rating", "Yếu")

    # Tính fund_score và dividend_score riêng
    fund_score = 0
    div_score  = 0
    try:
        fc = get_fund_check(symbol)
        fund_score = min(len(fc.get("held_by", [])) * 10, 20)
    except Exception:
        pass
    try:
        dv = get_dividend(symbol)
        if isinstance(dv, list) and len(dv) > 0:
            div_score = 15
    except Exception:
        pass

    # quality_score = tổng điểm tài chính (trừ phần quỹ và cổ tức)
    quality_score = max(0, score_value - fund_score - div_score)

    if score_value >= 85:   recommendation = "Tích sản dài hạn"
    elif score_value >= 65: recommendation = "Theo dõi thêm"
    elif score_value >= 40: recommendation = "Quan sát"
    else:                    recommendation = "Không ưu tiên"

    return {
        "symbol": symbol,
        "score": score_value,
        "rating": rating,
        "recommendation": recommendation,
        "quality_score": quality_score,
        "fund_score": fund_score,
        "dividend_score": div_score,
        "reasons": data.get("reasons", [])
    }

# =====================================
# HOLD — tổng hợp đầy đủ
# =====================================
@app.get("/hold/{symbol}")
def get_hold(symbol: str):
    symbol = symbol.upper()
    logger.info(f"/hold/{symbol}")
    try:
        price_data   = get_stock_price(symbol)
        quality_data = get_quality(symbol)
        fund_data    = get_fund_check(symbol)

        # Bổ sung: company overview
        company_data = None
        try:
            co = get_company(symbol)
            if isinstance(co, list) and co:
                company_data = co[0]
        except Exception:
            pass

        # Bổ sung: events (cổ tức + sự kiện)
        events_data = None
        try:
            ev = Reference().company(symbol).events()
            if ev is not None and not ev.empty:
                events_data = ev.head(5).to_dict(orient="records")
        except Exception:
            pass

        return {
            "symbol": symbol,
            "price": price_data,
            "quality": quality_data,
            "funds": fund_data,
            "company": company_data,
            "recent_events": events_data,
        }
    except Exception as e:
        logger.error(f"/hold/{symbol}: {e}")
        return _err(str(e))

# =====================================
# SO SÁNH 2 MÃ
# =====================================
@app.get("/compare/{symbol1}/{symbol2}")
def compare(symbol1: str, symbol2: str):
    return {
        symbol1.upper(): get_quality(symbol1),
        symbol2.upper(): get_quality(symbol2)
    }

# =====================================
# GỢI Ý TỪ QUỸ — sửa lỗi 500, log đầy đủ
# =====================================
@app.get("/recommend")
def recommend():
    logger.info("/recommend — bắt đầu quét holdings DCDS+DCDE")
    best: dict = {}

    for fund_name in ["DCDS", "DCDE"]:
        try:
            holdings = _fetch_fund_holdings(fund_name)
            code_col = _col(holdings, "stock_code", "symbol", "ticker")
            if not code_col:
                logger.warning(f"/recommend — {fund_name}: không tìm được cột mã")
                continue
            symbols = holdings[code_col].dropna().unique().tolist()
            logger.info(f"/recommend — {fund_name}: {len(symbols)} mã")
            for symbol in symbols:
                symbol = str(symbol).upper()
                if symbol in best:
                    continue
                try:
                    q = get_quality(symbol)
                    if isinstance(q, dict) and "score" in q:
                        best[symbol] = {
                            "symbol": symbol,
                            "score": q["score"],
                            "rating": q["rating"],
                            "recommendation": q["recommendation"]
                        }
                except Exception as e:
                    logger.warning(f"/recommend — {symbol}: {e}")
        except Exception as e:
            logger.error(f"/recommend — {fund_name}: {e}")

    result = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    logger.info(f"/recommend — trả về top {min(10, len(result))} mã")
    return result[:10]

# =====================================
# PORTFOLIO SCORE
# =====================================
@app.post("/portfolio-score")
def portfolio_score(data: PortfolioRequest):
    if not data.stocks:
        return _err("Danh sách cổ phiếu không được rỗng", 400)
    details = []
    total = 0
    for symbol in data.stocks:
        q = get_quality(symbol)
        total += q.get("score", 0) if isinstance(q, dict) else 0
        details.append(q)
    return {
        "portfolio_score": round(total / len(data.stocks), 2),
        "stock_count": len(data.stocks),
        "details": details
    }

# =====================================
# [MỚI] MARKET — chỉ số thị trường
# =====================================
@app.get("/market")
def get_market():
    result = {}
    indices = {
        "vnindex": "VNINDEX",
        "vn30":    "VN30",
        "hnx":     "HNX",
        "upcom":   "UPCOM"
    }
    for key, idx in indices.items():
        try:
            mkt = Market()
            # v4: index OHLCV
            df = mkt.index(idx).ohlcv(start=_days_ago(2), end=_today(), interval="1D")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                close_col = _col(df, "close", "Close") or df.columns[-2]
                result[key] = {
                    "index": idx,
                    "close": float(latest[close_col]),
                    "date": str(df.index[-1])[:10] if hasattr(df.index[-1], "__str__") else _today()
                }
            else:
                result[key] = {"index": idx, "close": None, "error": "no data"}
        except Exception as e:
            result[key] = {"index": idx, "close": None, "error": str(e)}
    return result

# =====================================
# [MỚI] NEWS — tin tức cổ phiếu (cache 1h)
# =====================================
@app.get("/news/{symbol}")
def get_news(symbol: str):
    symbol = symbol.upper()
    logger.info(f"/news/{symbol}")
    key = f"news_{symbol}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        ref = Reference()
        # v4: thử company().news() hoặc events()
        news_data = None
        try:
            news_data = ref.company(symbol).news()
        except Exception:
            pass
        if news_data is None or (hasattr(news_data, "empty") and news_data.empty):
            try:
                news_data = ref.company(symbol).events()
            except Exception:
                pass
        if news_data is None or (hasattr(news_data, "empty") and news_data.empty):
            return _err(f"Không có tin tức cho {symbol}", 404)

        # Chuẩn hóa output
        records = news_data.to_dict(orient="records") if hasattr(news_data, "to_dict") else news_data
        result = []
        for r in records[:20]:
            item = {}
            # tiêu đề
            for k in ["title", "headline", "tiêu_đề", "name", "event"]:
                if k in r and r[k]:
                    item["title"] = str(r[k]); break
            # thời gian
            for k in ["date", "time", "published_at", "publish_date", "ngay", "event_date"]:
                if k in r and r[k]:
                    item["date"] = str(r[k])[:19]; break
            # nguồn
            for k in ["source", "publisher", "url", "nguon"]:
                if k in r and r[k]:
                    item["source"] = str(r[k]); break
            if item:
                result.append(item)
        _cache_set(key, result, TTL_NEWS)
        return result
    except Exception as e:
        logger.error(f"/news/{symbol}: {e}")
        return _err(str(e))

# =====================================
# [MỚI] ANALYZE — tổng hợp toàn bộ
# =====================================
@app.get("/analyze/{symbol}")
def get_analyze(symbol: str):
    symbol = symbol.upper()
    logger.info(f"/analyze/{symbol}")
    result: dict = {"symbol": symbol}

    def safe_call(fn, *args):
        try:
            r = fn(*args)
            # Bỏ qua nếu là JSONResponse lỗi
            if hasattr(r, "status_code"):
                return None
            return r
        except Exception:
            return None

    result["price"]    = safe_call(get_stock_price, symbol)
    result["company"]  = safe_call(get_company, symbol)
    result["score"]    = safe_call(get_score, symbol)
    result["quality"]  = safe_call(get_quality, symbol)
    result["dividend"] = safe_call(get_dividend, symbol)
    result["funds"]    = safe_call(get_fund_check, symbol)
    result["news"]     = safe_call(get_news, symbol)
    return result

# =====================================
# [MỚI] INDEX — chỉ số đơn lẻ
# =====================================
@app.get("/index/{symbol}")
def get_index(symbol: str):
    symbol = symbol.upper()
    try:
        df = Market().index(symbol).ohlcv(start=_days_ago(30), end=_today(), interval="1D")
        if df is None or df.empty:
            return _err(f"Không có dữ liệu chỉ số {symbol}", 404)
        latest = df.iloc[-1]
        close_col = _col(df, "close", "Close") or df.columns[-2]
        vol_col   = _col(df, "volume", "Volume") or df.columns[-1]
        prev = df.iloc[-2] if len(df) >= 2 else None
        change_pct = None
        if prev is not None:
            prev_close = float(prev[close_col])
            cur_close  = float(latest[close_col])
            change_pct = round((cur_close - prev_close) / prev_close * 100, 2) if prev_close else None
        return {
            "symbol": symbol,
            "close": float(latest[close_col]),
            "volume": int(latest[vol_col]),
            "change_pct": change_pct,
            "history": df.tail(30).to_dict(orient="records")
        }
    except Exception as e:
        return _err(str(e))

# =====================================
# [MỚI] GROWTH STOCKS — ROE cao, EPS dương, D/E thấp
# =====================================
@app.get("/growth-stocks")
def get_growth_stocks():
    candidates = []
    for fund_name in ["DCDS", "DCDE", "DCBF"]:
        try:
            holdings = _fetch_fund_holdings(fund_name)
            code_col = _col(holdings, "stock_code", "symbol", "ticker")
            if not code_col:
                continue
            for sym in holdings[code_col].dropna().unique():
                sym = str(sym).upper()
                if any(c["symbol"] == sym for c in candidates):
                    continue
                try:
                    fin = get_financial_summary(sym)
                    if not isinstance(fin, dict) or fin.get("periods", 0) == 0:
                        continue
                    l = fin.get("latest", {})
                    roe = l.get("roe"); eps = l.get("eps"); debt = l.get("debt_to_equity")
                    if roe and roe >= 15 and eps and eps > 0:
                        candidates.append({
                            "symbol": sym,
                            "roe": roe,
                            "eps": eps,
                            "debt_to_equity": debt,
                            "pe": l.get("pe"),
                            "pb": l.get("pb"),
                        })
                except Exception:
                    pass
        except Exception:
            pass
    result = sorted(candidates, key=lambda x: x.get("roe", 0), reverse=True)
    return {"count": len(result), "stocks": result[:20]}

# =====================================
# [MỚI] DIVIDEND KINGS — cổ phiếu cổ tức cao
# =====================================
@app.get("/dividend-kings")
def get_dividend_kings():
    candidates = []
    for fund_name in ["DCDS", "DCDE", "DCBF"]:
        try:
            holdings = _fetch_fund_holdings(fund_name)
            code_col = _col(holdings, "stock_code", "symbol", "ticker")
            if not code_col:
                continue
            for sym in holdings[code_col].dropna().unique():
                sym = str(sym).upper()
                if any(c["symbol"] == sym for c in candidates):
                    continue
                try:
                    div = get_dividend(sym)
                    if not isinstance(div, list) or len(div) == 0:
                        continue
                    sc = get_score(sym)
                    score_val = sc.get("score", 0) if isinstance(sc, dict) else 0
                    candidates.append({
                        "symbol": sym,
                        "dividend_count": len(div),
                        "score": score_val,
                        "rating": sc.get("rating") if isinstance(sc, dict) else None
                    })
                except Exception:
                    pass
        except Exception:
            pass
    result = sorted(candidates, key=lambda x: (x.get("dividend_count", 0), x.get("score", 0)), reverse=True)
    return {"count": len(result), "stocks": result[:20]}