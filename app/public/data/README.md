# Default results workbook

The default results workbook (`.xlsx`) produced by notebook `08_export` will live here and ship with the
app. It has the sheets `manifest`, `kpis`, `strategy_comparison`, `budget_curve`, `turn_plans`,
`rotations` and `risk_heatmap`, as defined in [docs/data_contract.md](../../../docs/data_contract.md).

Users can upload a replacement workbook in the browser; it is validated against the data contract and can
be reset to this default. Keep the file small (well under 25 MB).
