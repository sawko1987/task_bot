import zipfile
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FILES_TO_PACK = [
    "index.py",
    "bot.py",
    "db.py",
    "models.py",
    "max_api.py",
    "config.py",
    "requirements.txt",
]

def make_zip(output_filename="deployment.zip"):
    with zipfile.ZipFile(output_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in FILES_TO_PACK:
            if os.path.exists(file):
                zipf.write(file, arcname=file)
                print(f"Добавлен файл: {file}")
            else:
                print(f"Предупреждение: файл {file} не найден!")
    print(f"\n✅ Архив успешно создан: {os.path.abspath(output_filename)}")

if __name__ == "__main__":
    make_zip()
