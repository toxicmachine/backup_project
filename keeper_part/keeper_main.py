import logging
import os
import subprocess
import re
from decouple import config

from keeper_functions import (get_item_logger, create_lock, check_and_clean_lock, remove_lock,
                              get_messages_from_rabbit, mount_backup_storage, umount_backup_storage,
                              start_copy_process, notify_message)

project_dir = config('PROJECT_DIR')
source_dir = config('SOURCE_DIR')
destination_dir = config('DESTINATION_DIR')

backup_keeper = config('BACKUP_KEEPER', cast=bool)

mount_settings = {
    'mount_disk': config('MOUNT_DISK'),
    'mount_dir': config('MOUNT_DIR'),
    'mount_user_uid': config('MOUNT_USER_UID'),
    'mount_user_gid': config('MOUNT_USER_GID'),
}

mail_settings = {
    'email_domain': config('EMAIL_DOMAIN'),
    'smtp_server': config('EMAIL_HOST'),
    'smtp_port': config('EMAIL_PORT'),
    'username': config('EMAIL_USER'),
    'password': config('EMAIL_PASS'),
    'info_message': config('EMAIL_INFO_MESSAGE'),
    'recipients': config('EMAIL_NOTIFY_LIST', cast=lambda v: [s.strip() for s in v.split(',')]),
}

rabbit_settings = {
    'rabbit_host': config('RABBIT_HOST'),
    'rabbit_port': config('RABBIT_PORT'),
    'rabbit_username': config('RABBIT_USERNAME'),
    'rabbit_password': config('RABBIT_PASSWORD'),
    'rabbit_exchange': config('RABBIT_EXCHANGE'),
    'rabbit_virtual_host': config('RABBIT_VIRTUAL_HOST'),
    'rabbit_get_queue': config('RABBIT_GET_QUEUE')
}
logger_main, handler_main = get_item_logger('keeper', every_launch=True)
if backup_keeper:
    lock_file = os.path.join(project_dir, 'copy_current_state.lock')
    check_copy_state = check_and_clean_lock(lock_file)
    if check_copy_state['status']:
        logger_main.info(f"Очередной старт работы BACKUP_KEEPER.")
        result_of_mount = mount_backup_storage(mount_settings)
        if result_of_mount['status']:
            logger_main.info(f"Проверка наличия новых сообщений в очереди {rabbit_settings['rabbit_get_queue']} на {rabbit_settings['rabbit_host']}.")
            result = get_messages_from_rabbit(rabbit_settings)
            if result:
                create_lock(lock_file)
                logger_main.info(f"Найдены новые сообщения в очереди {rabbit_settings['rabbit_get_queue']} на {rabbit_settings['rabbit_host']}. Количество: {len(result)}")
                for message in result:
                    logger_main.info(f"Начата работа с базой данных: {message['database_name']}.")
                    logger_db_name, handler_db_name = get_item_logger(message['database_name'])
                    logger_db_name.info(f"{'#' * 77}")
                    one_process = start_copy_process(source_dir, destination_dir, message)
                    if one_process['status']:
                        logger_db_name.info(f"Информация о создании РК {message['database_name']}: {one_process['backup_info']}")
                        logger_db_name.info(f"Информация о времени проверки РК {message['database_name']}: {one_process['restore_info']} секунд.")
                        logger_db_name.info(
                            f"Информация о копировании РК {message['database_name']} на сервер хранения: {one_process['copy_info']}")
                        logger_db_name.info(
                            f"Информация о размере РК {message['database_name']}: {one_process['file_size_info']}")
                        reply_body = f"""
                        {mail_settings['info_message']} {message['database_name']} :\n
                        Информация о создании РК {message['database_name']}: {one_process['backup_info']}\n
                        Информация о времени проверки РК {message['database_name']}: {one_process['restore_info']} секунд.\n
                        Информация о копировании РК {message['database_name']} на сервер хранения: {one_process['copy_info']}\n
                        Информация о размере РК {message['database_name']}: {one_process['file_size_info']}\n\n
                        Сообщение создано автоматически, не отвечайте на него!
                        """
                    else:
                        logger_db_name.info(f"Информация о работе с {message['database_name']}: {one_process['error']}")
                        reply_body = f"""
                        {mail_settings['info_message']} {message['database_name']} :\n
                        Информация о работе с {message['database_name']}: {one_process['error']}\n\n
                        Сообщение создано автоматически, не отвечайте на него!
                        """
                    attempt_send_message = notify_message(mail_settings,
                                                          f"Информация о работе BACKUP_KEEPER с БД {message['database_name']}",
                                                          reply_body)
                    if attempt_send_message['success']:
                        logger_db_name.info(f"Информация о работе BACKUP_KEEPER с БД {message['database_name']} отправлена на электронные почты {mail_settings['recipients']}.")
                    else:
                        logger_db_name.info(f"Сообщение о работе BACKUP_KEEPER с БД {message['database_name']} не удалось отправить, ошибка: {attempt_send_message['error']}.")
                    handler_db_name.close()
                    logger_db_name.removeHandler(handler_db_name)
                    logger_main.info(f"Закончена работа с базой данных: {message['database_name']}.")
                remove_lock(lock_file)
            else:
                logger_main.info(f"Новых сообщений в очереди {rabbit_settings['rabbit_get_queue']} на {rabbit_settings['rabbit_host']} нет.")
            result_of_umount = umount_backup_storage(mount_settings)
            logger_main.info(f"Результат размонтирования: {result_of_umount['message']}")
        else:
            logger_main.info(f"При монтировании диска возникла ошибка: {result_of_mount['error_message']}")
    else:
        logger_main.warning(f"Копирование резервных копий на сервер хранения идет в данный момент.")
else:
    logger_main.warning(f"Копирование резервных копий на сервер хранения выключено. Если это ошибка, проверьте переменную BACKUP_KEEPER в файле .env, в данный момент: {backup_keeper}.")
handler_main.close()
logger_main.removeHandler(handler_main)
logging.shutdown()