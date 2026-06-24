from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import vnstock

# =====================================
# VNSTOCK v4 — Unified UI
# Market / Reference / Fundamental / Retail
# =====================================
from vnstock import Market, Reference, Fundamental, Retail

app = FastAPI(
    title="VNStock API",
    version="4.0"
)

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
# HELPERS
# =====================================

def _today_str() -> str:
    from datetime import date
    return date.today().strftime("%Y-%m-%d")

def _safe_float(val):
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# =====================================
# META
# =====================================

@app.get("/")
def home():
    return {
        "status": "ok",
        "service": "vnstock-api",
        "version": "4.0",
        "vnstock": vnstock.__version__
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/ping")
def ping():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {
        "api_version": "4.0",
        "vnstock_version": vnstock.__version__
    }


@app.get("/info")
def info():
    return {
        "service": "vnstock-api",
        "version": "4.0",
        "vnstock_version": vnstock.__version__,
        "watched_funds": WATCHED_FUNDS,
        "endpoints": [
            "GET  /",
            "GET  /health",
            "GET  /ping",
            "GET  /version",
            "GET  /info",
            "GET  /stock/{symbol}",
            "GET  /company/{symbol}",
            "GET  /dividend/{symbol}",
            "GET  /financial-summary/{symbol}",
            "GET  /etf/{symbol}",
            "GET  /fund/{symbol}",
            "GET  /fund/{symbol}/top",
            "GET  /fund/{symbol}/industry",
            "GET  /fund-check/{symbol}",
            "GET  /fund-favorites",
            "GET  /score/{symbol}",
            "GET  /quality/{symbol}",
            "GET  /hold/{symbol}",
            "GET  /compare/{symbol1}/{symbol2}",
            "GET  /recommend",
            "POST /portfolio-score",
        ]
    }


# =====================================
# GIÁ CỔ PHIẾU MỚI NHẤT
# v4: Market().equity(symbol).ohlcv(start, end)
# =====================================

@app.get("/stock/{symbol}")
def get_stock_price(symbol: str):
    symbol = symbol.upper()
    try:
        from datetime import date, timedelta
        end = date.today().strftime("%Y-%m-%d")
        start = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")

        mkt = Market()
        quote = mkt.equity(symbol).ohlcv(start=start, end=end, interval="1D")

        if quote is None or quote.empty:
            raise HTTPException(status_code=404, detail=f"Không có dữ liệu giá cho {symbol}")

        latest = quote.iloc[-1]
        return {
            "symbol": symbol,
            "close": float(latest.get("close", latest.get("Close", 0))),
            "volume": int(latest.get("volume", latest.get("Volume", 0)))
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =====================================
# THÔNG TIN CÔNG TY
# v4: Reference().company(symbol).info()
# =====================================

@app.get("/company/{symbol}")
def get_company(symbol: str):
    symbol = symbol.upper()
    try:
        ref = Reference()
        data = ref.company(symbol).info()
        if data is None or (hasattr(data, 'empty') and data.empty):
            raise HTTPException(status_code=404, detail=f"Không tìm thấy công ty {symbol}")
        if hasattr(data, 'to_dict'):
            return data.to_dict(orient="records")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =====================================
# CỔ TỨC
# v4: Reference().company(symbol).events() hoặc capital_history()
# Fallback: Finance (v3 compat) nếu v4 không có dividend riêng
# =====================================

@app.get("/dividend/{symbol}")
def get_dividend(symbol: str):
    symbol = symbol.upper()
    try:
        # v4: thử Reference().company().events() lọc dividend
        ref = Reference()
        try:
            events = ref.company(symbol).events()
            if events is not None and not events.empty:
                # Lọc sự kiện liên quan cổ tức
                div_mask = events.apply(
                    lambda r: any(
                        kw in str(r).lower()
                        for kw in ["cổ tức", "dividend", "chi tra", "cash"]
                    ),
                    axis=1
                )
                divs = events[div_mask]
                if not divs.empty:
                    return divs.to_dict(orient="records")
        except Exception:
            pass

        # Fallback: Finance.ratio có thể chứa dividend yield
        from vnstock import Finance
        fin = Finance(symbol=symbol, source="KBS")
        ratio = fin.ratio(period="year", lang="en")
        if ratio is not None and not ratio.empty:
            cols = [c for c in ratio.columns if "dividend" in c.lower() or "div" in c.lower()]
            if cols:
                return ratio[["period"] + cols if "period" in ratio.columns else cols].head(8).to_dict(orient="records")

        raise HTTPException(status_code=404, detail=f"Không có dữ liệu cổ tức cho {symbol}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =====================================
# CHỈ SỐ TÀI CHÍNH
# v4: Fundamental().equity(symbol).ratios()
# =====================================

@app.get("/financial-summary/{symbol}")
def get_financial_summary(symbol: str):
    symbol = symbol.upper()
    try:
        fund = Fundamental()
        ratios = fund.equity(symbol).ratios()

        if ratios is None or ratios.empty:
            raise HTTPException(status_code=404, detail=f"Không có dữ liệu tài chính cho {symbol}")

        latest = ratios.iloc[0]

        # Mapping tên cột v4 (có thể khác v3)
        def get_val(*keys):
            for k in keys:
                # exact match
                if k in latest.index:
                    return _safe_float(latest[k])
                # case-insensitive
                for col in latest.index:
                    if k.lower() == str(col).lower():
                        return _safe_float(latest[col])
            return None

        return {
            "symbol": symbol,
            "periods": len(ratios),
            "latest": {
                "roe":           get_val("roe", "ROE", "return_on_equity"),
                "roa":           get_val("roa", "ROA", "return_on_assets"),
                "eps":           get_val("eps", "EPS", "earnings_per_share"),
                "pe":            get_val("pe", "PE", "price_to_earnings", "p_e"),
                "debt_to_equity":get_val("debt_to_equity", "D/E", "de_ratio"),
            },
            "history": ratios.head(5).to_dict(orient="records")
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =====================================
# ETF
# v4: Market().etf(symbol).ohlcv()
# =====================================

@app.get("/etf/{symbol}")
def get_etf(symbol: str):
    symbol = symbol.upper()
    try:
        from datetime import date, timedelta
        end = date.today().strftime("%Y-%m-%d")
        start = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        mkt = Market()
        data = mkt.etf(symbol).ohlcv(start=start, end=end)
        if data is None or data.empty:
            raise HTTPException(status_code=404, detail=f"Không có dữ liệu ETF {symbol}")
        return data.tail(10).to_dict(orient="records")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =====================================
# QUỸ MỞ — NAV
# v4: Market().fund(symbol).history() / nav()
# =====================================

@app.get("/fund/{symbol}")
def get_fund_nav(symbol: str):
    symbol = symbol.upper()
    try:
        mkt = Market()
        # v4 dùng nav() hoặc history()
        try:
            nav = mkt.fund(symbol).nav()
        except Exception:
            nav = mkt.fund(symbol).history()
        if nav is None or nav.empty:
            raise HTTPException(status_code=404, detail=f"Không có dữ liệu NAV cho quỹ {symbol}")
        return nav.tail(20).to_dict(orient="records")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/fund/{symbol}/top")
def get_fund_top_holdings(symbol: str):
    symbol = symbol.upper()
    try:
        # v4: Reference().fund(symbol).top_holding()
        ref = Reference()
        try:
            data = ref.fund(symbol).top_holding()
        except Exception:
            # fallback Market
            data = Market().fund(symbol).top_holding()
        if data is None or data.empty:
            raise HTTPException(status_code=404, detail=f"Không có top holdings cho quỹ {symbol}")
        return data.to_dict(orient="records")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/fund/{symbol}/industry")
def get_fund_industry(symbol: str):
    symbol = symbol.upper()
    try:
        ref = Reference()
        try:
            data = ref.fund(symbol).industry_holding()
        except Exception:
            data = Market().fund(symbol).industry_holding()
        if data is None or data.empty:
            raise HTTPException(status_code=404, detail=f"Không có dữ liệu phân bổ ngành cho quỹ {symbol}")
        return data.to_dict(orient="records")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =====================================
# HELPERS FUND (dùng chung)
# =====================================

def _fetch_fund_holdings(fund_name: str):
    """Lấy top holdings của quỹ, thử Reference trước rồi Market."""
    try:
        return Reference().fund(fund_name).top_holding()
    except Exception:
        return Market().fund(fund_name).top_holding()


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
            # Tên cột có thể là 'stock_code' hoặc 'symbol' tuỳ version
            code_col = next(
                (c for c in holdings.columns if c.lower() in ("stock_code", "symbol", "ticker")),
                None
            )
            weight_col = next(
                (c for c in holdings.columns if c.lower() in ("net_asset_percent", "weight", "allocation")),
                None
            )
            if code_col and symbol in holdings[code_col].values:
                row = holdings[holdings[code_col] == symbol].iloc[0]
                weight = _safe_float(row[weight_col]) if weight_col else None
                held_by.append({"fund": fund_name, "weight": weight or 0.0})
        except Exception:
            pass

    return {
        "symbol": symbol,
        "held_by": held_by,
        "fund_count": len(held_by)
    }


# =====================================
# CHẤM ĐIỂM CỔ PHIẾU
# Thang: Quỹ (40) | DN (10) | Cổ tức (20) | Tài chính (10)
#        ROE>=20 (15) | ROE>=15 (10) | D/E<1 (15) | EPS>0 (10)
#        Tổng tối đa ~120
# =====================================

@app.get("/score/{symbol}")
def get_score(symbol: str):
    symbol = symbol.upper()
    total_score = 0
    reasons = []

    # --- Quỹ nắm giữ ---
    fund_data = get_fund_check(symbol)
    held_by = fund_data.get("held_by", [])
    if held_by:
        fund_score = min(len(held_by) * 20, 40)
        total_score += fund_score
        for f in held_by:
            reasons.append(f"{f['fund']} nắm giữ {f['weight']:.2f}% NAV")

    # --- Dữ liệu doanh nghiệp ---
    try:
        info = Reference().company(symbol).info()
        if info is not None and (not hasattr(info, 'empty') or not info.empty):
            total_score += 10
            reasons.append("Có dữ liệu doanh nghiệp")
    except Exception:
        pass

    # --- Cổ tức (kiểm tra events hoặc ratio) ---
    try:
        div_res = get_dividend(symbol)
        if div_res and len(div_res) > 0:
            total_score += 20
            reasons.append(f"Có {len(div_res)} kỳ cổ tức/sự kiện")
    except Exception:
        pass

    # --- Tài chính & chỉ số ---
    try:
        fin_res = get_financial_summary(symbol)
        if fin_res and fin_res.get("periods", 0) > 0:
            total_score += 10
            reasons.append("Có dữ liệu tài chính")
            l = fin_res.get("latest", {})

            roe = l.get("roe")
            if roe is not None:
                if roe >= 20:
                    total_score += 15
                    reasons.append(f"ROE cao ({roe:.1f}%)")
                elif roe >= 15:
                    total_score += 10
                    reasons.append(f"ROE khá ({roe:.1f}%)")

            debt = l.get("debt_to_equity")
            if debt is not None and debt < 1:
                total_score += 15
                reasons.append(f"Nợ/vốn thấp ({debt:.2f})")

            eps = l.get("eps")
            if eps is not None and eps > 0:
                total_score += 10
                reasons.append(f"EPS dương ({eps:,.0f})")
    except Exception:
        pass

    if total_score >= 90:
        rating = "Xuất sắc"
    elif total_score >= 70:
        rating = "Rất tốt"
    elif total_score >= 50:
        rating = "Tốt"
    elif total_score >= 30:
        rating = "Theo dõi"
    else:
        rating = "Yếu"

    return {
        "symbol": symbol,
        "score": total_score,
        "rating": rating,
        "reasons": reasons
    }


# =====================================
# CHẤT LƯỢNG & KHUYẾN NGHỊ
# =====================================

@app.get("/quality/{symbol}")
def get_quality(symbol: str):
    data = get_score(symbol)
    score_value = data["score"]

    if score_value >= 90:
        recommendation = "Tích sản dài hạn"
    elif score_value >= 60:
        recommendation = "Theo dõi thêm"
    else:
        recommendation = "Không ưu tiên"

    return {
        "symbol": symbol.upper(),
        "score": score_value,
        "rating": data["rating"],
        "recommendation": recommendation,
        "reasons": data["reasons"]
    }


# =====================================
# HOLD — TỔNG HỢP
# =====================================

@app.get("/hold/{symbol}")
def get_hold(symbol: str):
    symbol = symbol.upper()
    try:
        price_data   = get_stock_price(symbol)
        quality_data = get_quality(symbol)
        fund_data    = get_fund_check(symbol)
        return {
            "symbol": symbol,
            "price": price_data,
            "quality": quality_data,
            "funds": fund_data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
# GỢI Ý TỪ QUỸ — duyệt holdings, lọc top score
# =====================================

@app.get("/recommend")
def recommend():
    best: dict = {}

    for fund_name in ["DCDS", "DCDE"]:
        try:
            holdings = _fetch_fund_holdings(fund_name)
            code_col = next(
                (c for c in holdings.columns if c.lower() in ("stock_code", "symbol", "ticker")),
                None
            )
            if not code_col:
                continue
            for _, row in holdings.iterrows():
                symbol = str(row[code_col]).upper()
                if symbol in best:
                    continue
                try:
                    q = get_quality(symbol)
                    best[symbol] = {
                        "symbol": symbol,
                        "score": q["score"],
                        "rating": q["rating"],
                        "recommendation": q["recommendation"]
                    }
                except Exception:
                    pass
        except Exception:
            pass

    result = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    return result[:10]


# =====================================
# PORTFOLIO SCORE
# =====================================

@app.post("/portfolio-score")
def portfolio_score(data: PortfolioRequest):
    if not data.stocks:
        raise HTTPException(status_code=400, detail="Danh sách cổ phiếu không được rỗng")

    details = []
    total = 0

    for symbol in data.stocks:
        q = get_quality(symbol)
        total += q["score"]
        details.append(q)

    return {
        "portfolio_score": round(total / len(data.stocks), 2),
        "stock_count": len(data.stocks),
        "details": details
    }
