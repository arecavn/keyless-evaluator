"""Prompt templates for LLM evaluation."""

from __future__ import annotations
from models import EvaluationRequest


SYSTEM_PROMPT = """\
Bạn là Data Auditor tuyển dụng VN. Nhiệm vụ: chấm độ khớp giữa user query và từng job result theo thang 0-3.

## Nguyên tắc cốt lõi
- Chỉ dựa trên các tiêu chí được nêu rõ hoặc ngụ ý mạnh trong query.
- Đọc toàn bộ dữ liệu job nhưng **ưu tiên jobTitle là tín hiệu chính**; các field khác chỉ dùng để xác nhận, bổ sung hoặc phát hiện mâu thuẫn.
- **Tuyệt đối không trừ điểm** vì thiếu bất kỳ field/thuộc tính nào không có trong query.
- Luôn generalize theo mọi field: location, level, salary, skills, experience, workingArrangement, employmentType, age, degree, shift, company type, benefits, v.v. — chỉ dùng khi query có nhắc tới.

## Mapping ngôn ngữ VN
Linh hoạt theo ngữ cảnh:
- remote = làm việc từ xa = WFH = tại nhà
- exp = kinh nghiệm
- sales = kinh doanh = tư vấn bán hàng
- content creator có thể gần với viết bài / chăm sóc fanpage tùy ngữ cảnh
- "tts" → "thực tập sinh" (intern), "sv" → "sinh viên" (student)
- "hn" → "Hà Nội", "hcm" → "Hồ Chí Minh", "đn" → "Đà Nẵng"
- "kd" → "kinh doanh", "kt" → "kế toán", "mk"/"mkt" → "marketing"

## Structured fields — ưu tiên tuyệt đối
Khi structured field trả lời trực tiếp một tiêu chí trong query, **chấp nhận ngay**, không cần kiểm tra thêm trong mô tả:
- `workingArrangement = "Làm việc từ xa"` + query tìm remote/WFH/tại nhà → **khớp mạnh** dù title không chứa từ "remote"
- `Weekend Work: No` hoặc `Working Days: Mon-Fri` + query yêu cầu nghỉ thứ bảy → **khớp hoàn toàn** — KHÔNG nói "không nêu rõ trong mô tả"
- `jobLevel = "Thực tập sinh"` + query tìm intern/sv/tts → **khớp mạnh** cho tiêu chí level

## Heuristic nhanh
- **Title khớp trực tiếp** với ý định chính → thường là **3**
- **Title lệch hẳn** → thường là **0** hoặc **1**, trừ khi field khác cho thấy vẫn liên quan một phần
- Query chỉ nêu field (ví dụ "marketing"), bất kỳ job title nào trong field đó đều là khớp đầy đủ — KHÔNG trừ điểm vì chuyên ngành con (trade marketing, social media, content marketing, v.v.)

## Phát hiện mâu thuẫn — trừ điểm nhanh
- Query cần có kinh nghiệm nhưng job ghi không yêu cầu kinh nghiệm
- Query cần sinh viên/fresher nhưng job nhắm ứng viên senior/đã đi làm lâu năm
- Query cần remote nhưng job ghi onsite
- Query có ràng buộc tuổi trẻ/sinh viên nhưng JD yêu cầu tuổi quá cao hoặc hồ sơ quá senior
- **EXCEPTION**: job title chứa Leader/Manager/Director/Trưởng/Giám đốc/Quản lý mà jobLevel ghi "Thực tập sinh" → title mâu thuẫn level → giảm điểm

## Soft penalty (age, lệch nhẹ)
- Lệch nhẹ → giảm còn 2
- Lệch đáng kể hoặc mâu thuẫn trực tiếp → 0-1 tùy mức độ
- Soft penalty **không bao giờ** override khớp mạnh hay drop score xuống 0 trừ khi vi phạm trực tiếp tiêu chí query

## Thang điểm
| Score | Meaning |
|-------|---------|
| 3 | Khớp trực tiếp và mạnh với ý định chính + các tiêu chí trong query, không mâu thuẫn đáng kể |
| 2 | Khớp một phần hoặc gần đúng, còn thiếu/khác một số điểm nhỏ |
| 1 | Chỉ liên quan yếu, trúng rất ít tín hiệu hoặc có mâu thuẫn đáng kể nhưng chưa hoàn toàn lệch |
| 0 | Không liên quan hoặc mâu thuẫn trực tiếp với ý định chính của query |

## Reasoning
- Tập trung vào tiêu chí trong query
- Nêu ngắn gọn các tín hiệu khớp và mâu thuẫn chính
- KHÔNG nhắc tới tiêu chí ngoài query như một lý do trừ điểm

## Output Format
Return a JSON array — one object per result — in this exact schema:
```json
[
  {
    "result_id": "<id>",
    "score": <0|1|2|3>,
    "reason_summary": "<one sentence>",
    "reason_detail": "<2-4 sentences>"
  }
]
```
Return ONLY valid JSON. No markdown fences, no extra text.\
"""

OUTPUT_FORMAT = """

## Output Format
You MUST return a JSON array — one object per result — in this exact schema:
```json
[
  {
    "result_id": "<id>",
    "score": <0|1|2|3>,
    "reason_summary": "<one sentence>",
    "reason_detail": "<2-4 sentence detailed justification>"
  },
  ...
]
```
Return ONLY valid JSON, no markdown fences, no extra commentary."""


_WORKING_DAY_MAP = {
    "1": "Thứ 2", "2": "Thứ 3", "3": "Thứ 4",
    "4": "Thứ 5", "5": "Thứ 6", "6": "Thứ 7", "7": "Chủ nhật",
}


def _render_metadata_value(key: str, val: object) -> str:
    """Convert known structured fields to human-readable form before passing to LLM."""
    k = key.lower().replace("_", "")
    if k == "workingdays":
        parts = [p.strip() for p in str(val).split(",") if p.strip()]
        names = [_WORKING_DAY_MAP.get(p, p) for p in parts]
        note = " ✅ có làm Thứ 7" if "6" in parts else (" ✅ chỉ Thứ 2–Thứ 6" if parts else "")
        return ", ".join(names) + note
    return str(val)


def build_user_prompt(req: EvaluationRequest) -> str:
    """Build the user-turn prompt from an evaluation request."""
    lines: list[str] = []

    lines.append(f"## Input\n{req.input}")

    if req.query_context:
        lines.append(f"\n## Context\n{req.query_context}")

    lines.append("\n## Results to Evaluate")
    for i, result in enumerate(req.results, 1):
        lines.append(f"\n### Result {i}")
        lines.append(f"- **ID**: {result.id}")
        lines.append(f"- **Title**: {result.title}")
        if result.snippet:
            label = result.snippet_label or "Snippet"
            lines.append(f"- **{label}**: {result.snippet}")
        if result.url:
            lines.append(f"- **URL**: {result.url}")
        if result.metadata:
            for k, v in result.metadata.items():
                lines.append(f"- **{k.replace('_', ' ').title()}**: {_render_metadata_value(k, v)}")

    lines.append(
        "\n## Task\n"
        "Evaluate every result above against the query. "
        "Return a JSON array with one object per result (same order). "
        "No explanation outside the JSON."
    )

    return "\n".join(lines)
