import re

string = """
Total objects: 1
Total size: 182.494 MiB (191358464 Byte)
"""

match = re.search(r'\(([0-9]+) Byte\)', string)

if match:
    # Убираем пробелы и запятые, преобразуем в число
    byte_str = match.group(1).replace(' ', '').replace(',', '')
    print(int(byte_str))
else:
    # Вернем None или вызовем ошибку, если размер не найден
    print(None)

