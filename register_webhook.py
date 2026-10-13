import sys
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def register(token: str, webhook_url: str):
    url = "https://platform-api2.max.ru/subscriptions"
    headers = {
        "Authorization": token.strip(),
        "Content-Type": "application/json"
    }
    payload = {
        "url": webhook_url.strip(),
        "update_types": ["message_created", "bot_started"]
    }
    print(f"Отправка запроса на регистрацию вебхука...")
    print(f"URL функции: {webhook_url.strip()}")
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        resp = requests.post(url, headers=headers, json=payload, verify=False, timeout=10)
        print(f"Статус ответа: {resp.status_code}")
        print(f"Ответ сервера: {resp.text}")
        if resp.status_code in (200, 201):
            print("\n Вебхук успешно зарегистрирован! Бот готов к работе.")
        else:
            print("\n Не удалось зарегистрировать вебхук. Проверьте правильность токена и ссылки.")
    except Exception as e:
        print(f"\n Ошибка при отправке запроса: {e}")

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        token = sys.argv[1]
        webhook_url = sys.argv[2]
    else:
        token = input("Введите токен бота MAX: ").strip()
        webhook_url = input("Введите ссылку на Cloud Function (https://functions.yandexcloud.net/...): ").strip()
    
    register(token, webhook_url)
