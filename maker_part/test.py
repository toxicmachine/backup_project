# import smtplib, ssl
# from email.header import Header
# from email.mime.multipart import MIMEMultipart
# from email.mime.text import MIMEText
#
# port = 465  # Для SSL
# smtp_server = "tegu.tosk68.ru"
# sender_email = "autopy@tosk68.ru"
# receiver_email = ["kuramshin@tosk68.ru" , "kral@tosk.tmb.ru"]
# password = "48RjkLjkDhfEuj"

import uuid
import yagmail

# Настройки почтового сервера
smtp_server = "tegu.tosk68.ru"
smtp_port = 465
email_address = "backup_info@tosk68.ru"
email_password = "49LdjVexE,kLhf"
email_domain = 'tosk68.ru'
email_list = ['kuramshin@tosk68.ru', 'kral@tosk.tmb.ru']
mail_settings = {
    "email_domain": email_domain,
    "smtp_server": smtp_server,
    "smtp_port": smtp_port,
    "username": email_address,
    "password": email_password,
    "recipients": email_list,
}

def notify_message(settings, reply_subject, reply_body, **kwargs):
    try:
        yag = yagmail.SMTP(user=settings['username'], password=settings['password'],
                           host=settings['smtp_server'])
        message_id = f"<{uuid.uuid4()}@{settings['email_domain']}>"
        yag.send(
            to=settings['recipients'],
            headers={"Message-ID": message_id},
            subject=reply_subject,
            contents=reply_body,
        )
        yag.close()
        return {'success': True}
    except Exception as e:
        return {'success': False, 'error': e}

notify_message(mail_settings, 'Информация о выполнении резервного копирования', 'Проверочное письмо!')
