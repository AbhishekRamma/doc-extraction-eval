# Golden dataset attribution

The receipts in this folder (`images/`, `labels.json`, `cord_gt_raw.json`) come from
**CORD v2** (Consolidated Receipt Dataset), test split, 100 receipts.

- Source: official Hugging Face dataset <https://huggingface.co/datasets/naver-clova-ix/cord-v2>
  (dataset commit `7f0115a4b758a71d6473b8d085751692da2fef98`), originally from <https://github.com/clovaai/cord>
- Licence: Creative Commons Attribution 4.0 International (CC BY 4.0),
  <https://creativecommons.org/licenses/by/4.0/>
- Authors / owner: NAVER Clova AI

## Citation

Park, S., Shin, S., Lee, B., Lee, J., Surh, J., Seo, M., & Lee, H. (2019).
*CORD: A Consolidated Receipt Dataset for Post-OCR Parsing.*
Workshop on Document Intelligence at NeurIPS 2019.

## Changes we made

- Images were re-encoded from PNG to JPEG (quality 95) and renamed
  `cord_test_000.jpg` ... `cord_test_099.jpg` (number = CORD `image_id`).
- `labels.json` keeps only the receipt total (`gt_parse.total.total_price`),
  normalised to a plain 2-decimal string (e.g. `"60.000"` -> `"60000.00"`, Indonesian Rupiah).
  Five receipts have no `total_price` in CORD, so their total is `null`.
- `cord_gt_raw.json` is the unmodified `gt_parse` for each receipt.

## Missing merchant and date labels

`merchant` and `date` are always `null`. NAVER removed the `store_info` fields
(store name, address, date, etc.) from the public CORD release, so there is no
ground truth for them and they are not scored.
