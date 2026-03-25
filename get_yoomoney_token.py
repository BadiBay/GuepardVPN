from yoomoney import Authorize


def main():
    print("=== Получение токена ЮMoney ===")
    client_id = input("Введите Идентификатор приложения (client_id): ").strip()

    # Добавили запрос секретного слова
    client_secret = input("Введите Секретное слово (client_secret): ").strip()

    redirect_uri = input("Введите Redirect URI (ссылку из настроек): ").strip()

    if not client_id or not redirect_uri or not client_secret:
        print("Ошибка: Вы ввели не все данные!")
        return

    print("\n⏳ Сейчас скрипт сгенерирует ссылку. Если браузер не откроется сам,")
    print("скрипт напечатает ссылку в консоли — скопируйте её и откройте вручную.")

    try:
        Authorize(
            client_id=client_id,
            client_secret=client_secret,  # Передаем секрет в библиотеку
            redirect_uri=redirect_uri,
            scope=[
                "account-info",
                "operation-history",
                "operation-details",
                "incoming-transfers",
                "payment-p2p",
                "payment-shop"
            ]
        )
    except Exception as e:
        print(f"\n❌ Произошла ошибка при подключении к ЮMoney: {e}")


if __name__ == "__main__":
    main()