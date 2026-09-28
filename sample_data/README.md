# Sample data (made up)

A fictional four-company family ("Acme") in the same shape as the D&B files. **It is not real data.**
CI runs the pipeline on it, because the real input is confidential and kept out of the repository.

It deliberately includes the cases the pipeline must handle:
- DUNS with leading zeros
- dates at year, month and day precision, plus a `null` date
- "missing" written as `{}`, `[]`, `null` and as an absent key
- a consolidated employee figure that is **second** in its list
- the same industry code at two priorities
- a field the model ignores (`tradeStyleNames`)
