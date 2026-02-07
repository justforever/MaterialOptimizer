def solve_cutting_stock_optimized(demands, stock_sizes):
    # 1. 排序：大料優先，這是所有裝箱問題的基礎
    demands.sort(reverse=True)
    
    bins = []
    # 複製一份需求清單，用來追蹤哪些已經被處理過
    remaining_demands = demands.copy()

    while remaining_demands:
        best_combination = None
        best_stock_size = -1
        min_waste = float('inf')
        
        # 針對每一種原材規格進行模擬
        for size in stock_sizes:
            # 策略：嘗試填滿當前這支板材
            # 我們從剩下最大的料開始試，看這支板材能裝進哪些料
            current_test_item = remaining_demands[0]
            if size < current_test_item:
                continue
            
            # 模擬填料過程 (簡單的 Best Fit 組合)
            temp_used_indices = [0]
            current_sum = current_test_item
            
            for i in range(1, len(remaining_demands)):
                if current_sum + remaining_demands[i] <= size:
                    current_sum += remaining_demands[i]
                    temp_used_indices.append(i)
            
            waste = size - current_sum
            # 評價標準：剩餘越少越好
            if waste < min_waste:
                min_waste = waste
                best_stock_size = size
                best_combination = temp_used_indices

        # 根據最佳評估結果，正式移除需求並建立板材
        if best_combination:
            items_to_add = [remaining_demands[i] for i in best_combination]
            bins.append({
                "size": best_stock_size,
                "items": items_to_add,
                "waste": round(min_waste, 3)
            })
            # 由後往前刪除，避免 index 跑掉
            for i in sorted(best_combination, reverse=True):
                remaining_demands.pop(i)
        else:
            # 如果連最大的板材都裝不下某個需求
            print(f"無法處理長度: {remaining_demands[0]}")
            break
            
    return bins

# 數據輸入
demands = [1.1, 1.1, 1.1, 1.1, 1.43, 1.53, 1.7, 1.7, 1.7, 2.15, 2.3, 2.3, 2.3, 2.3, 2.45, 3.2, 3.3, 3.3, 3.6, 3.6, 3.8, 3.8, 3.802, 3.802, 4.08, 4.08, 4.22, 4.22, 4.22, 4.97, 5.18, 5.2, 5.3, 5.5, 5.62, 5.95, 6.05]
stock_sizes = [9, 10, 11, 12, 13, 14]

results = solve_cutting_stock_optimized(demands, stock_sizes)

# 顯示結果
total_waste = 0
print(f"{'規格':<5} | {'剩餘':<6} | {'裁切組合'}")
print("-" * 45)
for b in results:
    print(f"{b['size']:<5} | {b['waste']:<6} | {b['items']}")
    total_waste += b['waste']

print("-" * 45)
print(f"總剩餘長度 (廢料): {round(total_waste, 3)}")
print(f"總使用支數: {len(results)}")