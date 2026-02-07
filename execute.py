import math

# 需求長度
demands = [1.1, 1.1, 1.1, 1.1, 1.43, 1.53, 1.7, 1.7, 1.7, 2.15, 2.3, 2.3, 2.3, 2.3, 2.45, 3.2, 3.3, 3.3, 3.6, 3.6, 3.8, 3.8, 3.802, 3.802, 4.08, 4.08, 4.22, 4.22, 4.22, 4.97, 5.18, 5.2, 5.3, 5.5, 5.62, 5.95, 6.05]
# 規格長度
stock_sizes = [9, 10, 11, 12, 13, 14]

def best_fit_decreasing(demands, stock_sizes):
    # 1. 由大到小排序
    demands.sort(reverse=True)
    stock_sizes.sort(reverse=True)
    
    # 用來存放已開啟的板材：每個元素是 {"size": 規格, "rem": 剩餘, "items": [內容]}
    bins = []
    
    for item in demands:
        best_bin_index = -1
        min_waste = float('inf')
        
        # 2. 嘗試放入現有板材 (找剩餘空間最剛好的)
        for i, b in enumerate(bins):
            if b["rem"] >= item:
                waste = b["rem"] - item
                if waste < min_waste:
                    min_waste = waste
                    best_bin_index = i
        
        if best_bin_index != -1:
            # 放入現有板材
            bins[best_bin_index]["items"].append(item)
            bins[best_bin_index]["rem"] -= item
        else:
            # 3. 開啟新板材 (找最適合的規格)
            best_stock_size = -1
            min_new_waste = float('inf')
            
            for size in stock_sizes:
                if size >= item:
                    waste = size - item
                    if waste < min_new_waste:
                        min_new_waste = waste
                        best_stock_size = size
            
            if best_stock_size != -1:
                bins.append({
                    "size": best_stock_size,
                    "rem": best_stock_size - item,
                    "items": [item]
                })
            else:
                print(f"警告：找不到夠大的規格來裝 {item}")

    return bins

# 執行計算
result_bins = best_fit_decreasing(demands, stock_sizes)

# 輸出結果
total_waste = 0
print(f"{'規格':<5} | {'剩餘':<6} | {'內容 (長度組合)'}")
print("-" * 40)
for b in result_bins:
    rem = round(b['rem'], 3)
    total_waste += rem
    items_str = ", ".join(map(str, b['items']))
    print(f"{b['size']:<5} | {rem:<6} | {items_str}")

print("-" * 40)
print(f"總剩餘長度 (廢料): {round(total_waste, 3)}")
print(f"總使用支數: {len(result_bins)}")