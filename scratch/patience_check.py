check_interval = 30
gw_hung = 10
fe_hung = 45
gw_budget = 240
fe_budget = 1200
print("check_interval =", check_interval, "s")
print("Gateway: threshold =", gw_hung, "x", check_interval, "=", gw_hung*check_interval, "s ; launcher budget =", gw_budget, "s ; margin =", gw_hung*check_interval - gw_budget, "s")
print("Frontend: threshold =", fe_hung, "x", check_interval, "=", fe_hung*check_interval, "s ; launcher budget =", fe_budget, "s ; margin =", fe_hung*check_interval - fe_budget, "s")
print("PREVIOUS BUG: 6 x 30 =", 6*30, "s < launcher 240 s budget -> margin", 6*30-240, "s (NEGATIVE)")
