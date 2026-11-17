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


RESET_SUBJECT = "Đặt lại mật khẩu mmlabs / Reset your mmlabs password"


def reset_message(link: str) -> tuple[str, str]:
    body = (
        "Xin chào,\n\n"
        "Bấm vào liên kết dưới đây để đặt mật khẩu mới cho tài khoản mmlabs của bạn. "
        "Liên kết dùng được một lần và hết hạn sau 30 phút. Sau khi đặt lại, mọi phiên đăng "
        "nhập của tài khoản sẽ kết thúc.\n\n"
        f"{link}\n\n"
        "Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này; mật khẩu của bạn "
        "không thay đổi.\n\n"
        "---\n\n"
        "Hello,\n\n"
        "Open the link below to set a new password for your mmlabs account. "
        "The link works once and expires in 30 minutes. After the reset, every signed-in "
        "session of the account ends.\n\n"
        f"{link}\n\n"
        "If you did not ask to reset your password, you can ignore this email; your password "
        "is unchanged.\n\n"
        "mmlabs\n"
    )
    return RESET_SUBJECT, body


INVITE_SUBJECT = "Lời mời tham gia tổ chức / You're invited to an organization"


def invitation_message(link: str, app_name: str, org_name: str | None) -> tuple[str, str]:
    org = f' "{org_name}"' if org_name else ""
    body = (
        "Xin chào,\n\n"
        f"Bạn được mời tham gia một tổ chức{org} trên {app_name}. Đăng nhập bằng đúng địa "
        "chỉ email này, rồi bấm vào liên kết dưới đây để chấp nhận. Liên kết dùng được một lần "
        "và sẽ hết hạn.\n\n"
        f"{link}\n\n"
        "Nếu bạn không biết về lời mời này, hãy bỏ qua email này.\n\n"
        "---\n\n"
        "Hello,\n\n"
        f"You have been invited to join an organization{org} on {app_name}. Sign in with "
        "this exact email address, then open the link below to accept. The link works once "
        "and will expire.\n\n"
        f"{link}\n\n"
        "If you do not know about this invitation, you can ignore this email.\n\n"
        "mmlabs\n"
    )
    return INVITE_SUBJECT, body
