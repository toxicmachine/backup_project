import json
import logging
import os
import re
import ssl
import subprocess
import time
import uuid
from datetime import datetime
import pika
import yagmail


def create_lock(lock_file):
    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])
    boot_time = int(time.time() - uptime_seconds)  # Время запуска системы (UNIX timestamp)
    with open(lock_file, 'w') as f:
        f.write(str(boot_time))

def check_and_clean_lock(lock_file):
    if not os.path.exists(lock_file):
        return {'status': True,}

    with open(lock_file, 'r') as f:
        stored_boot_time = int(f.read().strip())
    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])
    current_boot_time = int(time.time() - uptime_seconds)

    # Если система была перезагружена после создания lock-файла
    if stored_boot_time != current_boot_time:
        os.remove(lock_file)
        return {'status': True,}
    # print(f"Скрипт уже выполняется. Файл блокировки найден: {lock_file}")
    return {'status': False}  # Процесс уже выполняется

def check_lock(lock_file):
    if os.path.exists(lock_file):
        # print(f"Скрипт уже выполняется. Файл блокировки найден: {lock_file}")
        return {'status': True,}
    else:
        return {'status': False}

def remove_lock(lock_file):
    try:
        os.remove(lock_file)
    except FileNotFoundError:
        # Если файла нет, ничего страшного
        pass

def get_item_logger(item_name, **kwargs):
    """
    :param item_name: название файла лога
    :return: сам логгер и его хэндлер
    """
    every_launch = kwargs.pop('every_launch', None)
    formatted_date = datetime.now().strftime("%Y-%m-%d")
    formatted_date_time = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    if every_launch is None:
        dir_path = os.path.join("logs", formatted_date)
        os.makedirs(dir_path, exist_ok=True)
        logger = logging.getLogger(f'{item_name}_logger')
        logger.setLevel(logging.INFO)
        log_file_path = os.path.join(dir_path, f'{item_name}.log')
    else:
        dir_path = os.path.join("logs", 'keeper_logs')
        os.makedirs(dir_path, exist_ok=True)
        logger = logging.getLogger(f'{item_name}_logger')
        logger.setLevel(logging.INFO)
        log_file_path = os.path.join(dir_path, f'{formatted_date_time}_{item_name}.log')
    file_handler = logging.FileHandler(log_file_path)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger, file_handler

def get_messages_from_rabbit(rabbit_settings):
    context = ssl.create_default_context()
    credentials = pika.PlainCredentials(rabbit_settings['rabbit_username'], rabbit_settings['rabbit_password'])
    parameters = pika.ConnectionParameters(host=rabbit_settings['rabbit_host'],
                                           port=rabbit_settings['rabbit_port'],
                                           virtual_host=rabbit_settings['rabbit_virtual_host'],
                                           credentials=credentials,
                                           ssl_options=pika.SSLOptions(context))
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    new_messages = []
    try:
        while True:
            method_frame, header_frame, body = channel.basic_get(queue=rabbit_settings['rabbit_get_queue'], auto_ack=True)
            if not method_frame or method_frame.NAME == 'Basic.GetEmpty':
                break  # Нет больше сообщений в очереди
            data = json.loads(body.decode())
            new_messages.append(data)
    finally:
        connection.close()
    return new_messages

def mount_backup_storage(mount_settings):
    umount_cmd = ['sudo', 'umount', f'{mount_settings['mount_dir']}']
    umount_result = subprocess.run(umount_cmd, capture_output=True, text=True)
    command = [
        'sudo', 'mount', '-t', 'exfat',
        '-o', f'ro,uid={mount_settings['mount_user_uid']},gid={mount_settings['mount_user_gid']}',
        f'{mount_settings['mount_disk']}', f'{mount_settings['mount_dir']}'
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return {
            'status': True,
            'message': 'Диск успешно смонтирован.'
        }
    else:
        return {
            'status': False,
            'message': f'Ошибка монтирования: {result.stderr or "Код возврата " + str(result.returncode)}'
        }

def umount_backup_storage(mount_settings):
    umount_cmd = ['sudo', 'umount', f"{mount_settings['mount_dir']}"]
    umount_result = subprocess.run(umount_cmd, capture_output=True, text=True)
    message = umount_result.stdout or umount_result.stderr or "OK"
    success = umount_result.returncode == 0
    return {
        'success': success,
        'message': message,
        'return_code': umount_result.returncode
    }

def start_copy_process(source_dir, destination_dir, one_message):
    keys_to_check = ['database_name', 'backup_name', 'backup_info', 'restore_info']
    keys_existence = all(key in one_message for key in keys_to_check)
    if keys_existence:
        file_path = os.path.join(source_dir, one_message['database_name'], one_message['backup_name'])
        if os.path.isfile(file_path):
            # проверка на существование в директории хранения
            check_file_exist = os.path.join(destination_dir, one_message['database_name'], one_message['backup_name'])
            if os.path.isfile(check_file_exist):
                return {'status': False,
                        'error': f'Файл резервной копии {file_path} уже скопирован в директорию хранения: {check_file_exist}.'
                        }
            response = {
                'status': True,
                'backup_info': one_message['backup_info'],
                'restore_info': one_message['restore_info'],
            }
            result_of_copy = deep_copy_action(file_path, check_file_exist)
            if result_of_copy['status']:
                size_of_file = get_file_size(check_file_exist)
                if result_of_copy['copy_info'] is not None:
                    response['copy_info'] = result_of_copy['copy_info']
                else:
                    response['copy_info'] = None
                if size_of_file['status']:
                    response['file_size_info'] = f"Размер резервной копии: {size_of_file['gibibytes_size']} ГБ"
                else:
                    response['file_size_info'] = f"Размер резервной копии вычислить не удалось."
                return response
            else:
                return {'status': False,
                        'error': f'{result_of_copy["error_message"]}'}
        else:
            return {'status': False,
                    'error': f'Файл резервной копии {file_path} не найден.'
                    }
    else:
        return {'status': False,
                'error': f'В сообщении {one_message} отсутствует необходимый ключ.'
                }

def deep_copy_action(source_file, destination_file):
    destination_folder = os.path.dirname(destination_file)
    os.makedirs(destination_folder, exist_ok=True)
    copy_cmd = [
        "rclone", "copyto",
        f"{source_file}",
        f"{destination_file}",
        "--checksum",
        "--progress"
    ]
    result = subprocess.run(copy_cmd, capture_output=True, text=True)
    output_lines = result.stdout.splitlines()
    last_lines = output_lines[-5:]
    if result.returncode != 0:
        return {
            'status': False,
            'error_message': f'Ошибка rclone (код {result.returncode}): {result.stderr}',
            'return_code': result.returncode
        }
    time_match = None
    speed_match = None
    for line in last_lines:
        if 'Elapsed time:' in line:
            time_match = line.replace('Elapsed time:', '').strip()
        if 'MiB/s' in line:
            match = re.search(r'([\d.]+) MiB/s', line)
            if match:
                speed_match = match.group(1)
    if time_match and speed_match:
        return {'status': True,
                'copy_info': f'Время копирования: {time_match}. Последняя зафиксированная скорость: {speed_match} MiB/s.',
                'last_lines': last_lines,
                }
    else:
        return {'status': True,
                'copy_info': None,
                'last_lines': last_lines,
        }

def get_file_size(file_path):
    get_size_cmd = [
        "rclone", "size",
        f"{file_path}",
    ]
    result = subprocess.run(get_size_cmd, capture_output=True, text=True)
    match = re.search(r'\(([0-9]+) Byte\)', result.stdout)
    if match:
        byte_str = match.group(1).replace(' ', '').replace(',', '')
        size_in_bytes = int(byte_str)
        gibibytes = size_in_bytes / (1024 ** 3)
        return {'status': True,
                'bytes_size': size_in_bytes,
                'gibibytes_size': round(gibibytes, 2),
                }
    else:
        return {'status': False,
                'error_message': 'Не удалось вычислить размер файла.'
                }

def notify_message(settings, reply_subject, reply_body):
    """
    :param settings: настройки пользователя почтового сервера
    :param reply_subject: тема письма
    :param reply_body: тело письма
    :return: результат отправки сообщения на электронную почту
    """
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
        return {'success': True,
                }
    except Exception as e:
        return {'success': False,
                'error': e
                }