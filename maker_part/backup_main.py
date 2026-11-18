import os
import logging
import time

from decouple import config
from backup_functions import (get_item_logger, read_configs, get_current_day, backup_database, check_backup_restore,
                              push_to_rabbit, clear_directory_contents, notify_message, build_table_for_message)

backups_dir = config('BACKUPS_DIR')
project_dir = config('PROJECT_DIR')
configs_dir = config('CONFIG_DIRS')
backups_maker = config('BACKUPS_MAKER', cast=bool)

server = config('DATABASE_HOST')
username = config('DATABASE_USER')
password = config('DATABASE_PASSWORD')

database_settings = {
    'server': config('DATABASE_HOST'),
    'username': config('DATABASE_USER'),
    'password': config('DATABASE_PASSWORD'),
}

databases_list = config('DATABASE_LIST', cast=lambda v: [s.strip() for s in v.split(',')])

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
    'rabbit_push_queue': config('RABBIT_PUSH_QUEUE')
}

if server == 'localhost':
    if not os.path.exists(backups_dir):
        os.makedirs(backups_dir)

rows_for_email = []
start_time = time.time()
logger_main, handler_main = get_item_logger('backup_maker')
logger_main.info(f"{'#' * 77}")
logger_main.info(f"Начало работы BACKUP_MAKER.")
logger_main.info(f"Чтение конфигураций из модуля(директории): {project_dir}\\{configs_dir}.")
all_configs = read_configs(configs_dir, databases_list, logger_main)
if backups_maker:
    logger_main.info(f"Попытка очистить директорию резервных копий: {backups_dir}.")
    result_of_remove = clear_directory_contents(backups_dir)
    if result_of_remove:
        logger_main.info(f"Директория резервных копий {backups_dir} успешно очищена.")
    else:
        logger_main.warning(f"Не удалось очистить директорию резервных копий {backups_dir}.")
        logger_main.warning(f"{result_of_remove['error']}")
    current_day = get_current_day()
    logger_main.info(f"Определен день недели: {current_day}.")
    for db_name, config in all_configs.items():
        logger_main.info(f"Начата работа с базой данных: {config['database']}.")
        logger_db_name, handler_db_name = get_item_logger(config['database'])
        logger_db_name.info(f"{'#' * 77}")
        row_for_message = [config['database'], '?', '?', '?']
        try:
            if config['state']:
                if config['schedule'][current_day]['state']:
                    if config['schedule'][current_day]['mode'] == 'default':
                        logger_db_name.info(f"Попытка выполнить действия в день недели {current_day} в режиме: {config['schedule'][current_day]['mode']}.")
                        result_of_backup = backup_database(database_settings, backups_dir, config['database'], logger_db_name)
                        if result_of_backup['status']:
                            result_of_check = check_backup_restore(database_settings, backups_dir, config['database'], result_of_backup['backup_name'], logger_db_name)
                            if result_of_check['success']:
                                logger_db_name.info(f"Попытка отправки данных о резервной копии в {rabbit_settings['rabbit_host']}.")
                                result_of_push = push_to_rabbit(config['database'], result_of_backup['backup_name'], result_of_backup['backup_info'], result_of_check['execution_time_seconds'], rabbit_settings)
                                if result_of_push:
                                    logger_db_name.info(f"Сообщение в {rabbit_settings['rabbit_host']} успешно отправлено.")
                                    row_for_message = [config['database'], 'Успешно', 'Успешно', 'Успешно']
                                else:
                                    logger_db_name.warning(f"При отправке в {rabbit_settings['rabbit_host']} возникла ошибка.")
                                    logger_db_name.warning(f"{result_of_push['error']}")
                                    row_for_message = [config['database'], 'Успешно', 'Успешно', 'Ошибка']
                            else:
                                # здесь должно быть удаление неудачной копии
                                logger_db_name.info(f"Попытка удалить неудачную резервную копию: {result_of_backup['backup_name']}.")
                                row_for_message = [config['database'], 'Успешно', 'Ошибка', '-']
                        else:
                            logger_db_name.info(f"При создании резервной копии возникла ошибка: {result_of_backup['error']}.")
                            row_for_message = [config['database'], 'Ошибка', '-', '-']
                    elif config['schedule'][current_day]['mode'] == 'extended':
                        # возможно в будущем добавить какие-то вариации резервной копии
                        logger_db_name.info(f"Попытка выполнить действия в режиме: {config['schedule'][current_day]['mode']}.")
                else:
                    logger_db_name.warning(f"В {current_day} резервное копирование выключено.")
                    row_for_message = [config['database'], '-', '-', '-']
            else:
                logger_db_name.warning(f"Резервное копирование выключено.")
                row_for_message = [config['database'], '-', '-', '-']
        except KeyError as e:
            # Обработка ожидаемой ошибки (нет ключа)
            logger_db_name.error(f"[ОШИБКА КОНФИГУРАЦИИ] Отсутствует ключ: {e}")
        except Exception as e:
            # Обработка ВСЕХ ОСТАЛЬНЫХ, неожиданных ошибок
            logger_db_name.error(f"[НЕПРЕДВИДЕННАЯ ОШИБКА] Тип: {type(e).__name__}, Сообщение: {e}")
        handler_db_name.close()
        logger_db_name.removeHandler(handler_db_name)
        logger_main.info(f"Закончена работа с базой данных: {config['database']}.")
        if config['schedule'][current_day]['notify_email']:
            rows_for_email.append(row_for_message)
else:
    logger_main.warning(f"Резервное копирование баз данных выключено. Если это ошибка, проверьте переменную BACKUPS_MAKER в файле .env, в данный момент: {backups_maker}.")
end_time = time.time()
execution_duration = end_time - start_time
if backups_maker:
    attempt_send_message = notify_message(mail_settings, 'Информация о выполнении резервного копирования',
                                          build_table_for_message(rows_for_email,
                                                                  f"{mail_settings['info_message']} Время выполнения на сервере: {execution_duration:.2f} секунд."))
else:
    attempt_send_message = notify_message(mail_settings,'Информация о выполнении резервного копирования',
                                          f"Резервное копирование баз данных выключено. Если это ошибка, проверьте переменную BACKUPS_MAKER в файле .env, в данный момент: {backups_maker}."
                                          )
if attempt_send_message['success']:
    logger_main.info(f"Сообщение о выполнение резервного копирования отправлено на электронные почты: {mail_settings['recipients']}.")
else:
    logger_main.info(f"Сообщение о выполнение резервного копирования не удалось отправить, ошибка: {attempt_send_message['error']}.")
logger_main.info(f"Конец работы BACKUP_MAKER.")
handler_main.close()
logger_main.removeHandler(handler_main)
logging.shutdown()
