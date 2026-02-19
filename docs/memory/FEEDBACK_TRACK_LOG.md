# Feedback Track Log

Updated: 2026-02-18

## Trạng thái hiện tại

1. `process` | `high` | `closed-loop`
- Feedback: Bắt buộc track log + note feedback quan trọng, nội hóa để không tái phạm.
- Enforced by: `data/state/user_feedback.jsonl` + guardrail directive trong prompt system.
- Verify: `GET /api/feedback/snapshot`.

2. `log_quality` | `high` | `in-progress`
- Feedback: Log còn nhiễu, cần AI tóm tắt insight thay vì spam log thô.
- Enforced by: ưu tiên summary/report trong dashboard + lưu feedback thành directive.
- Verify: kiểm tra Streamline report và Event feed filtering.

3. `realtime` | `high` | `in-progress`
- Feedback: Send Command/chat phải trả lời realtime rõ vị trí hiển thị.
- Enforced by: chat report payload + realtime panel flow.
- Verify: `POST /api/chat/ask` và quan sát response trong khung chat.

4. `orchestration` | `high` | `in-progress`
- Feedback: Guardian/Monitor phải auto gỡ kẹt và có quyền can thiệp hợp lệ.
- Enforced by: monitor autopilot + supervisor recovery.
- Verify: `GET/POST /api/monitor/autopilot`, `POST /api/agent/command`.

5. `observability` | `high` | `in-progress`
- Feedback: Phải thấy rõ số luồng runtime/terminal/agent, tên và nhiệm vụ.
- Enforced by: multi-instance status và runtime telemetry.
- Verify: `GET /api/orion/instances`, `GET /api/status`.

6. `provider` | `high` | `locked`
- Feedback: Không lệch provider, ưu tiên Z.ai endpoint.
- Enforced by: provider base config đã chuẩn hóa sang Z.ai.
- Verify: runtime/provider status endpoints + env/config kiểm tra.

7. `token` | `high` | `in-progress`
- Feedback: tối ưu token/hiệu năng liên tục.
- Enforced by: compact status snapshot + giảm payload/log thừa.
- Verify: model usage + routing/event volume theo phiên.

8. `governance` | `high` | `in-progress`
- Feedback: Supervisor phải nắm nguyên tắc tổng thể và điều phối tránh xung đột.
- Enforced by: guardrail directives ưu tiên cao + cơ chế monitor/supervisor.
- Verify: snapshot của prompt directives và hành vi điều phối khi stuck.

9. `ux_control` | `high` | `in-progress`
- Feedback: UI/UX chưa rõ, log cần newest ở trên, thao tác interactive phải trực quan hơn.
- Enforced by: cải thiện Control Dock + thứ tự log hiển thị + gợi ý lệnh rõ hơn.
- Verify: kiểm tra Guardian/Orion log hiển thị newest-first + thao tác nhanh hiển thị rõ.

10. `autonomy_reliability` | `high` | `in-progress`
- Feedback: hệ thống tự vận hành còn yếu, có lúc dừng sau 15-20 phút và chờ user nhập.
- Enforced by: ưu tiên autopilot/auto-resume, giảm manual pause không cần thiết.
- Verify: chạy liên tục trong thời gian dài, không yêu cầu user can thiệp.

11. `openclaw_visibility` | `medium` | `in-progress`
- Feedback: cần thấy rõ OpenClaw Control UI ở đâu để kiểm soát.
- Enforced by: thêm lối mở OpenClaw từ dashboard.
- Verify: có nút mở OpenClaw UI trong dashboard.

12. `full_autonomy` | `high` | `in-progress`
- Feedback: không muốn bị hỏi xác nhận; hệ thống phải tự động xử lý end-to-end.
- Enforced by: auto-approve + auto-execute decisions/commands mặc định.
- Verify: command intervention không tạo pending decision, tự chạy ngay.

13. `openclaw_headless` | `medium` | `in-progress`
- Feedback: không muốn phải dùng UI OpenClaw để điều khiển; cần điều khiển trực tiếp qua gateway/CLI.
- Enforced by: tích hợp OpenClaw headless control (CLI/gateway).
- Verify: có endpoint/command chạy OpenClaw automation không cần mở UI.

14. `plan_first_execution` | `high` | `in-progress`
- Feedback: trước khi code phải lập kế hoạch sâu và chi tiết (cách giải, phối hợp, tài nguyên, rủi ro, validation), không nhảy thẳng vào viết code.
- Enforced by: bắt buộc qua `plan-first gate` trong mỗi vòng xử lý task lớn (problem framing -> solution plan -> execution design -> risk/rollback -> validation plan) rồi mới implement.
- Verify: log mỗi task có section kế hoạch trước section thay đổi code; giảm lỗi “fix nóng” gây regression.

15. `no_prompt_autonomy` | `high` | `in-progress`
- Feedback: hệ thống phải full-auto, không hỏi lại user liên tục; không đòi user nhập tay nhiều bước.
- Enforced by: bật guardrail `AUTONOMY_NO_HUMAN_PROMPTS` mặc định + auto-resolve intervention/human-input bằng intake/default.
- Verify: queue pending_decisions giảm về 0 trong luồng thường; `human_input_request` chuyển thành `responded/auto_resolved` mà không cần thao tác tay.

## Nguồn dữ liệu chuẩn

- Feedback raw store: `data/state/user_feedback.jsonl`
- Guardrail policy: `docs/memory/USER_FEEDBACK_GUARDRAILS.md`
- Feedback API:
- `GET /api/feedback/snapshot`
- `POST /api/feedback/log`
