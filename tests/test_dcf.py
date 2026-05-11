#!/usr/bin/env python3
"""
測試 DCF 估值計算，使用用戶提供的範例（8464 股票）
"""
import pandas as pd
import numpy as np

def calc_dcf_fair_value(eps, dividend_payout_ratio=0.62, growth_rate=0.03, discount_rate=0.08):
    """
    DCF (Dividend Discount Model) 折現現金流估值
    公式：Fair Value = EPS × Dividend Payout Ratio × (1 + Growth Rate) / (Discount Rate - Growth Rate)
    
    Args:
        eps: 每股收益 (Earnings Per Share)
        dividend_payout_ratio: 配息率 (default 0.62 = 62%)
        growth_rate: 長期成長率 (default 0.03 = 3%)
        discount_rate: 折現率 (default 0.08 = 8%)
    
    Returns:
        fair_value: 理論價值，若參數無效則返回 None
    """
    if eps is None or eps <= 0:
        return None
    if dividend_payout_ratio < 0 or dividend_payout_ratio > 1:
        return None
    if growth_rate < 0 or growth_rate >= discount_rate:
        return None
    if discount_rate <= 0:
        return None
    
    numerator = eps * dividend_payout_ratio * (1 + growth_rate)
    denominator = discount_rate - growth_rate
    
    if denominator <= 0:
        return None
        
    fair_value = numerator / denominator
    
    if np.isnan(fair_value) or np.isinf(fair_value) or fair_value <= 0:
        return None
        
    return float(fair_value)

# 用戶的 DCF 範例數據
# EPS = 24.5
# 配息率 = 62%
# 成長率 = 3%
# 折現率 = 8%
# 期望答案 ≈ 313元

print("=" * 60)
print("DCF 估值測試 (范例: 8464)")
print("=" * 60)

# 測試參數
eps = 24.5
dividend_payout_ratio = 0.62
growth_rate = 0.03
discount_rate = 0.08

# 計算
dcf_value = calc_dcf_fair_value(
    eps=eps,
    dividend_payout_ratio=dividend_payout_ratio,
    growth_rate=growth_rate,
    discount_rate=discount_rate
)

print(f"\n輸入參數：")
print(f"  - EPS: {eps}")
print(f"  - 配息率: {dividend_payout_ratio*100:.1f}%")
print(f"  - 長期成長率: {growth_rate*100:.1f}%")
print(f"  - 折現率: {discount_rate*100:.1f}%")

print(f"\n計算過程：")
numerator = eps * dividend_payout_ratio * (1 + growth_rate)
denominator = discount_rate - growth_rate
print(f"  分子: {eps} × {dividend_payout_ratio} × {1 + growth_rate} = {numerator:.4f}")
print(f"  分母: {discount_rate} - {growth_rate} = {denominator:.4f}")
print(f"  公式: {numerator:.4f} / {denominator:.4f}")

print(f"\n計算結果：")
print(f"  DCF 理論價值: {dcf_value:.2f} 元")
print(f"  用戶期望值: 313 元")
print(f"  誤差範圍: {abs(dcf_value - 313) / 313 * 100:.2f}%")

if abs(dcf_value - 313) < 1:
    print("\n✅ 測試通過！")
else:
    print("\n⚠️  計算結果有差異（可能因浮點精度）")

# 安全邊際測試
print("\n" + "=" * 60)
print("安全邊際計算範例")
print("=" * 60)

current_price = 420
target_mos = 0.15  # 15% 安全邊際

# 使用 DCF 估值
fair_value = dcf_value
target_buy_price = fair_value * (1 - target_mos)

current_mos = (fair_value - current_price) / fair_value

print(f"\n參數：")
print(f"  - 理論價值（DCF）: {fair_value:.2f} 元")
print(f"  - 目前市價: {current_price:.2f} 元")
print(f"  - 目標安全邊際: {target_mos*100:.1f}%")

print(f"\n計算結果：")
print(f"  - 目前實際 MOS: {current_mos:.2%}")
print(f"  - 進場買價: {target_buy_price:.2f} 元 (以下買進)")

if current_price <= target_buy_price:
    print(f"  - 狀態: ✅ 滿足進場條件")
else:
    gap = target_buy_price - current_price
    print(f"  - 狀態: ❌ 尚未到達買進價")
    print(f"  - 還需下跌: {gap:.2f} 元 ({gap/current_price*100:.2f}%)")

print("\n" + "=" * 60)
print("測試完成")
print("=" * 60)
