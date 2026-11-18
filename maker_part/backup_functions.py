import importlib
import json
import os
import pkgutil
import shutil
import ssl
import subprocess
import sys
import uuid
import yagmail
import pika
import time
import logging
from pathlib import Path
from datetime import datetime
from types import ModuleType

def get_item_logger(item_name):
    """
    :param item_name: название файла лога
    :return: сам логгер и его хэндлер
    """
    formatted_date = datetime.now().strftime("%Y-%m-%d")
    dir_path = os.path.join("../logs", formatted_date)
    os.makedirs(dir_path, exist_ok=True)
    logger = logging.getLogger(f'{item_name}_logger')
    logger.setLevel(logging.INFO)
    log_file_path = os.path.join(dir_path, f'{item_name}.log')
    file_handler = logging.FileHandler(log_file_path)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger, file_handler

def read_configs(configs_dir, databases_list, logger_main):
    """
    :param configs_dir: модуль(директория), где хранятся файлы конфигурации резервного копирования для баз данных
    :param databases_list: переменная из .env, в которой указано какие базы обрабатываются и в каком порядке
    :param logger_main: хэндлер основного лога, нужен для записи действий
    :return: прочтенные, отсортированные конфигурации, согласно переменной databases_list
    """
    project_root = Path(__file__).parent
    sys.path.append(str(project_root))
    all_db_configs = {}
    databases_package = importlib.import_module(f'{configs_dir}')
    for finder, name, ispkg in pkgutil.iter_modules(databases_package.__path__):
        if name.startswith('__'):
            continue
        try:
            # Динамически импортируем модуль по его имени
            module: ModuleType = importlib.import_module(f'{configs_dir}.{name}')
            # 4. Проверяем, есть ли в модуле словарь SETTINGS
            if hasattr(module, 'SETTINGS'):
                # Если есть — добавляем его в наш общий словарь
                # Ключом будет имя файла (например, 'book'), а значением — сам словарь SETTINGS
                all_db_configs[name] = module.SETTINGS
                # print(f"  -> Настройки для '{name}' успешно загружены.")
            else:
                logger_main.warning(f"ВНИМАНИЕ: В файле {name}.py нет переменной SETTINGS. Он будет пропущен.")
        except Exception as e:
            logger_main.warning(f"Ошибка при загрузке {name}.py: {e}")
    ordered_configs = {name: all_db_configs[name] for name in databases_list if name in all_db_configs}
    return ordered_configs

def clear_directory_contents(dir_path):
    """
    :param dir_path: директория, которую необходимо очистить от файлов и папок
    :return: результат очищения директории
    """
    for filename in os.listdir(dir_path):
        file_path = os.path.join(dir_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.remove(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            return {'success': False,
                    'error': f"Не удалось удалить {file_path}. Причина: {e}"
                    }
    return {'success': True,}

def get_current_day():
    """
    :return: текущий день в виде: Monday, Tuesday и.т.д.
    """
    today = datetime.now()
    day_name = today.strftime('%A')
    return day_name

def backup_database(database_settings, backups_dir, database_name, logger_db_name):
    """
    :param database_settings: настройки подключения к базе данных
    :param backups_dir: директория, куда будут записаны резервные копии
    :param database_name: название базы данных
    :param logger_db_name: хэндлер лога работы с этой базой данных
    :return: результат создания резервной копии
    """
    current_datetime = datetime.now()
    formatted_date = current_datetime.strftime("%Y-%m-%d_%H-%M-%S")
    if database_settings['server'] == 'localhost':
        database_backups_dir = os.path.join(backups_dir, database_name)
        if not os.path.exists(database_backups_dir):
            os.makedirs(database_backups_dir)
    logger_db_name.info(f"Запущено создание резервной копии: {formatted_date}_{database_name}.bak.")
    sql_query = f"""
    BACKUP DATABASE [{database_name}]
    TO DISK = N'{backups_dir}\\{database_name}\\{formatted_date}_{database_name}.bak'
    WITH CHECKSUM, NAME = N'{formatted_date}_{database_name}', STATS = 10"""
    command = [
        'sqlcmd',
        '-S', database_settings['server'],
        '-U', database_settings['username'],
        '-P', database_settings['password'],
        '-Q', sql_query,
    ]
    result = subprocess.run(command, capture_output=True, text=False, encoding='cp866')
    backup_state = check_backup_creation(result)
    if backup_state['success']:
        logger_db_name.info(f"Резервная копия: {formatted_date}_{database_name}.bak создана.")
        logger_db_name.info(f"Информация о выполнении: {backup_state['create_backup_info']}")
        return {'status': True,
                'backup_name': f'{formatted_date}_{database_name}.bak',
                'backup_info': backup_state['create_backup_info'],
                }
    else:
        logger_db_name.warning(f"Неудачное создание резервной копии: {formatted_date}_{database_name}.bak.")
        return {'status': False,
                'error': backup_state['error'],
                }

def check_backup_creation(output_stdout):
    """
    :param output_stdout: вывод команды, создающей резервную копию базы данных
    :return: результат проверки
    """
    if (output_stdout.returncode == 0) and ("BACKUP DATABASE успешно" in output_stdout.stdout):
        return {
            'success': True,
            'create_backup_info': output_stdout.stdout.splitlines()[-1],
        }
    else:
        return {
            'success': False,
            'error': output_stdout.stdout,
        }

def check_backup_restore(database_settings, backups_dir, database_name, backup_name, logger_db_name):
    """
    :param database_settings: настройки подключения к базе данных
    :param backups_dir: директория, куда будут записаны резервные копии
    :param database_name: название базы данных
    :param backup_name: название созданной резервной копии
    :param logger_db_name: хэндлер лога работы с этой базой данных
    :return: результат проверки резервной копии
    """
    backup_path = os.path.join(backups_dir, database_name, backup_name)
    if not os.path.isfile(backup_path):
        logger_db_name.info(f"Ошибка: Файл резервной копии не найден по пути: {backup_path}.")
        return False
    start_time = time.time()
    logger_db_name.info(f"Файл резервной копии найден. Запущена проверка.")
    sql_query = f"""
    RESTORE VERIFYONLY
    FROM DISK = N'{backups_dir}\\{database_name}\\{backup_name}';
    """
    command = [
        'sqlcmd',
        '-S', database_settings['server'],
        '-U', database_settings['username'],
        '-P', database_settings['password'],
        '-Q', sql_query,
    ]
    result = subprocess.run(command, capture_output=True, text=False, encoding='cp866')
    output = result.stdout
    is_successful = (result.returncode == 0) and ("Резервный набор данных для файла 1 правильный" in output)
    end_time = time.time()
    execution_duration = end_time - start_time
    if is_successful:
        logger_db_name.info(f"Проверка резервной копии завершена успешно. Время выполнения: {execution_duration:.2f} секунд.")
        return {
            'success': True,
            'execution_time_seconds': f"{execution_duration:.2f}",
        }
    else:
        logger_db_name.info(f"Ошибка при проверке резервной копии. Время выполнения: {execution_duration:.2f} секунд.")
        logger_db_name.info(f"STDERR:, {result.stderr}")
        return {
            'success': False,
            'execution_time_seconds': f"{execution_duration:.2f}",
            'error': result.stderr,
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

def build_table_for_message(rows, info_line=None):
    """
    :param info_line: информационное сообщение перед таблицей
    :param rows: список строк с указанием результата работы различных процессов
    :return: таблица для добавления в тело письма при отправке сообщения на электронную почту
    """
    headers = ["Название БД", "Создание РК", "Проверка РК", "Отправка в RabbitMQ"]
    header_row = " | ".join(headers)
    separator = f"{'#' * 55}"
    data_rows = [
        " | ".join(row) for row in rows
    ]
    table_body = "\n".join([header_row, separator] + data_rows)
    if info_line:
        return f"{info_line}\n\n{table_body}"
    return table_body

def push_to_rabbit(database_name, backup_name, backup_info, restore_info, rabbit_settings):
    """
    :param database_name: название базы данных
    :param backup_name: название созданной резервной копии
    :param backup_info: статистические данные при создании резервной копии
    :param restore_info: время проверки резервной копии
    :param rabbit_settings: настройки подключения к rabbitmq
    :return: результат отправки сообщения в rabbitmq
    """
    context = ssl.create_default_context()
    context.check_hostname = False  # Отключаем проверку соответствия имен
    context.verify_mode = ssl.CERT_NONE  # Не проверяем сертификаты

    credentials = pika.PlainCredentials(rabbit_settings['rabbit_username'], rabbit_settings['rabbit_password'])
    parameters = pika.ConnectionParameters(host=rabbit_settings['rabbit_host'],
                                           port=rabbit_settings['rabbit_port'],
                                           virtual_host='/',
                                           credentials=credentials,
                                           ssl_options=pika.SSLOptions(context),
                                           socket_timeout=5,
                                           )
    try:
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        channel.queue_declare(queue=rabbit_settings['rabbit_push_queue'], durable=True)
        data = {'database_name': database_name,
                'backup_name': backup_name,
                'backup_info': backup_info,
                'restore_info': restore_info,
                }
        json_string = json.dumps(data)
        channel.basic_publish(exchange=rabbit_settings['rabbit_exchange'], routing_key=rabbit_settings['rabbit_push_queue'], body=json_string)
        connection.close()
        return {'success': True,}
    except pika.exceptions.AMQPConnectionError as e:
        return {'success': False,
                'error': f"Ошибка соединения: {e.args}",
                }
    except pika.exceptions.ConnectionClosed as e:
        return {'success': False,
                'error': f"Соединение закрыто: {e.args}",
                }
    except pika.exceptions.AMQPChannelError as e:
        return {'success': False,
                'error': f"Ошибка канала: {e.args}",
                }
    except Exception as e:
        return {'success': False,
                'error': f"Другая ошибка: {e.args}",
                }
