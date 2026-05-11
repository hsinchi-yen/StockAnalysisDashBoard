import re

def patch(file):
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()

    if '"risk_score": risk_score' in content:
        return # already patched

    # 1. replace payload
    content = re.sub(
        r'("criteria": criteria,)',
        r'\1\n        "risk_score": risk_score,\n        "risk_criteria": risk_criteria,',
        content
    )

    # 2. insert risk logic
    risk_logic = """    # ── 排雷指標 (Risk Avoidance / Sell Signals) ──────────────────────────────
    risk_score = 0
    risk_criteria: list[dict[str, Any]] = []

    def _add_risk(cid: str, label: str, passed: bool | None, value: Any, value_label: str, threshold: str, warning: str | None = None) -> None:
        nonlocal risk_score
        if passed is False:
            risk_score -= 1
        risk_criteria.append({
            "id": cid, "label": label, "pass": passed,
            "value": value, "value_label": value_label,
            "threshold": threshold, "warning": warning
        })

    try:
        current_price = None
        # Try to get current price for risk checks
        df_daily_temp = client.fetch_stock_price(sid, date(today.year - 1, today.month, 1), today)
        if not df_daily_temp.empty:
            current_price = float(df_daily_temp.iloc[-1]["close"])

        # Fetch detailed financial statements for Risk
        df_bs = client.fetch_balance_sheet(sid, fetch_start_ext, today)
        df_fs = client.fetch_financial_statements(sid, fetch_start_ext, today)
        df_cf = client.fetch_cash_flows_statement(sid, fetch_start_ext, today)
        
        ocf_ni_ratio: float | None = None
        interest_cov: float | None = None
        intangible_ratio: float | None = None
        nonop_ratio: float | None = None

        if not df_fs.empty and not df_cf.empty and not df_bs.empty:
            q_dates = sorted(df_fs["date"].dropna().unique())
            if q_dates:
                latest_q = q_dates[-1]
                
                # Risk 4: Intangible Ratio
                bs_latest = df_bs[df_bs["date"] == latest_q]
                total_assets = bs_latest[bs_latest["type"] == "TotalAssets"]["value"].sum()
                intangible = bs_latest[bs_latest["type"] == "IntangibleAssets"]["value"].sum()
                if total_assets > 0:
                    intangible_ratio = round((intangible / total_assets) * 100, 2)
                
                # Risk 5: Non-operating Ratio
                fs_latest = df_fs[df_fs["date"] == latest_q]
                pretax = fs_latest[fs_latest["type"] == "PreTaxIncome"]["value"].sum()
                nonop = fs_latest[fs_latest["type"] == "TotalNonoperatingIncomeAndExpense"]["value"].sum()
                if pretax > 0:
                    nonop_ratio = round((nonop / pretax) * 100, 2)
                    
                # Risk 3: Interest Coverage
                interest_exp = df_cf[(df_cf["date"] == latest_q) & (df_cf["type"] == "InterestExpense")]["value"].sum()
                if interest_exp > 0:
                    ebit = pretax + interest_exp
                    interest_cov = round(ebit / interest_exp, 2)
                elif interest_exp == 0 and pretax > 0:
                    interest_cov = 999.0 # Safe if no interest
                    
                # Risk 2: OCF / NI (Annualized)
                latest_year = pd.Timestamp(latest_q).year
                ni_annual = df_fs[(df_fs["date"].dt.year == latest_year) & (df_fs["type"] == "IncomeAfterTaxes")]["value"].sum()
                ocf_annual = df_cf[(df_cf["date"].dt.year == latest_year) & (df_cf["type"].isin(["NetCashInflowFromOperatingActivities", "CashFlowsFromOperatingActivities"]))]["value"].sum()
                if ni_annual > 0:
                    ocf_ni_ratio = round((ocf_annual / ni_annual) * 100, 2)

        _add_risk("intangible_ratio", "無形資產占比 < 20%", (intangible_ratio < 20.0) if intangible_ratio is not None else None, 
                  intangible_ratio, f"{intangible_ratio:.1f}%" if intangible_ratio is not None else "無資料", "< 20%")
        _add_risk("nonop_ratio", "業外收支占比 < 50%", (nonop_ratio < 50.0) if nonop_ratio is not None else None, 
                  nonop_ratio, f"{nonop_ratio:.1f}%" if nonop_ratio is not None else "無資料", "< 50%")
        _add_risk("interest_coverage", "利息保障倍數 > 2.0", (interest_cov > 2.0) if interest_cov is not None else None, 
                  interest_cov, f"{interest_cov:.1f}x" if interest_cov is not None and interest_cov != 999.0 else ("無負債" if interest_cov == 999.0 else "無資料"), "> 2.0x")
        _add_risk("ocf_ni", "盈餘含金量 (OCF/NI) > 70%", (ocf_ni_ratio > 70.0) if ocf_ni_ratio is not None else None, 
                  ocf_ni_ratio, f"{ocf_ni_ratio:.1f}%" if ocf_ni_ratio is not None else "無資料", "> 70%")

        # [Risk 6] 技術面破線 (股價 < MA20 及 MA60)
        ma_pass: bool | None = None
        ma20: float | None = None
        ma60: float | None = None
        if current_price is not None:
            if len(df_daily_temp) >= 60:
                df_daily_temp["MA20"] = df_daily_temp["close"].rolling(20).mean()
                df_daily_temp["MA60"] = df_daily_temp["close"].rolling(60).mean()
                ma20 = float(df_daily_temp.iloc[-1]["MA20"])
                ma60 = float(df_daily_temp.iloc[-1]["MA60"])
                ma_pass = current_price > ma20 or current_price > ma60
                
        _add_risk("technical_trend", "技術面維持多頭 (未破月季線)", ma_pass, 
                  current_price, f"現價 {current_price:.1f} vs 月 {ma20:.1f} / 季 {ma60:.1f}" if ma20 else "無資料", "> MA20/MA60")

    except Exception as exc:
        warnings.append(f"risk_criteria: {exc}")

    # ── v3 scoring"""
    content = content.replace("    # ── v3 scoring", risk_logic)

    # 3. Add penalty adjustment
    penalty_logic = """    if pass_rate >= 75.0:
        recommendation, recommendation_label = "recommended_buy", "建議買進"
        signal, signal_label = "strong_buy", "建議買進"
    elif pass_rate >= 60.0:
        recommendation, recommendation_label = "scale_in", "可分批買進"
        signal, signal_label = "buy", "可分批買進"
    elif pass_rate >= 50.0:
        recommendation, recommendation_label = "watchlist", "觀察清單"
        signal, signal_label = "watch", "觀察清單"
    else:
        recommendation, recommendation_label = "not_recommended", "不建議買進"
        signal, signal_label = "neutral", "不建議買進"

    # 如果踩到 2 個以上的地雷，強制降級
    if risk_score <= -2 and pass_rate >= 50.0:
        warnings.append(f"觸發 {abs(risk_score)} 項排雷警示，已強制下調買入評等")
        recommendation, recommendation_label = "watchlist", "高風險避開"
        signal, signal_label = "avoid", "高風險避開"
"""
    content = re.sub(r'    if pass_rate >= 75\.0:.*?signal_label = "不建議買進"', penalty_logic, content, flags=re.DOTALL)

    with open(file, 'w', encoding='utf-8') as f:
        f.write(content)

patch("api.py")
patch("android_app/app/src/main/python/api.py")
