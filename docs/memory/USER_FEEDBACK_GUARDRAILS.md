# User Feedback Guardrails

Last updated: 2026-02-15

## Mục tiêu bất biến (Non-Negotiable)

1. UI/UX phải đỉnh cao nhưng tối giản:
- Mặc định light theme.
- Không rối, không spam panel, không giật/chập chờn.
- Ưu tiên rõ ràng, trực quan, dễ thao tác cho nhiều agent cùng lúc.

2. Hỏi lệnh phải thấy trả lời realtime:
- `Send Command`/chat phải hiển thị phản hồi ngay trong khung hội thoại.
- Streamline report phải dễ đọc, có insight và hành động kế tiếp.

3. Guardian/Monitor phải hành động, không chỉ quan sát:
- Tự gỡ kẹt và đẩy Orion chạy tiếp cho luồng an toàn thường quy.
- Có cơ chế can thiệp quyết định rõ ràng (approve/deny/continue) khi gặp command intervention.

4. Bắt buộc điều khiển đa Orion:
- Hỗ trợ theo từng instance (`orion-1..n`).
- Hiển thị rõ: đang có bao nhiêu luồng chạy, tên luồng, nhiệm vụ hiện tại.

5. Toàn vẹn provider/model:
- Ưu tiên cấu hình do user chỉ định (hiện tại: Z.ai OpenAI-compatible endpoint).
- Không tự lệch provider trái chỉ định.

6. Tối ưu token và hiệu năng:
- Giảm log thô, tăng tóm tắt có giá trị.
- Giảm payload/prompt phình to.
- Tránh fallback loop gây nhiễu và tốn chi phí.

7. Cơ chế ghi nhớ feedback phải bền vững:
- Mọi feedback quan trọng phải được track log + note lại.
- Mỗi feedback phải được chuyển thành guardrail để chống tái phạm ở vòng sau.

8. Supervisor phải hiểu tổng thể dự án:
- Nắm nguyên tắc user, tình trạng hệ thống và luồng thực thi hiện tại.
- Điều phối tránh xung đột giữa Monitor/Guardian/Orion.

## Quy tắc vận hành suy ra từ feedback

1. Mọi khiếu nại mức cao phải được lưu vào `data/state/user_feedback.jsonl` và nâng cấp thành directive.
2. Trước khi báo hoàn thành, bắt buộc tự kiểm:
- realtime response visibility
- multi-instance control/health
- supervisor recovery behavior
- provider route correctness
- token/performance impact
- log signal quality
3. Nếu còn lỗi ở bất kỳ mục nào, tiếp tục lặp cải tiến; không dừng ở trạng thái “tạm được”.
