"""Plain-text email copy (Vietnamese first, then English)."""

VERIFY_SUBJECT = "Xác nhận email mmlabs / Verify your mmlabs email"


def verification_message(link: str) -> tuple[str, str]:
    body = (
        "Xin chào,\n\n"
        "Bấm vào liên kết dưới đây để xác nhận địa chỉ email cho tài khoản mmlabs của bạn. "
        "Liên kết dùng được một lần và hết hạn sau 24 giờ.\n\n"
        f"{link}\n\n"
        "Nếu bạn không tạo tài khoản mmlabs, hãy bỏ qua email này.\n\n"
        "---\n\n"
        "Hello,\n\n"
        "Open the link below to verify the email address of your mmlabs account. "
        "The link works once and expires in 24 hours.\n\n"
        f"{link}\n\n"
        "If you did not create an mmlabs account, you can ignore this email.\n\n"
        "mmlabs\n"
    )
    return VERIFY_SUBJECT, body
