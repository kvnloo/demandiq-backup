// Link-level growth methods (NCHRP 255 Ch. 4 / NCHRP 765 Ch. 6 / ODOT APM Ch. 6).
// All but Ratio add the model's change in vehicles, so they need K.
export const METHODS = [
  { id: 'ratio', label: 'Ratio (growth)', help: 'Count × model future ÷ model base. NCHRP 765 default; works without K.' },
  { id: 'difference', label: 'Difference (incremental)', help: 'Count + model change. Best where the model grows a lot or starts near zero.' },
  { id: 'combined', label: 'Average of ratio and difference', help: 'NCHRP 255 averaging. NCHRP 765 no longer recommends it.' },
  { id: 'weighted', label: 'Weighted growth', help: 'Ratio leaning toward difference as model growth rises (NCHRP 765 Table 6-2, ODOT).' },
  { id: 'modified_average', label: 'Modified average', help: 'Average of weighted growth and difference (NCHRP 765 Table 6-2, ODOT).' },
  { id: 'nchrp765', label: 'Auto — NCHRP 765 rule', help: 'Picks per leg by how far the count is from the model (Table 6-2).' },
  { id: 'odot', label: 'Auto — ODOT decision tree', help: 'Picks per leg by model change and method agreement (ODOT APM Ch. 6).' },
]
