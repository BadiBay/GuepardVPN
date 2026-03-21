import os
import sys
import time

def start_tunnel():
    print("Запуск локального сервера...")
    os.system("start py -m http.server 8000 -d c:\\VPN\\webapp")
    
    print("Подключение к ngrok...")
    try:
        from pyngrok import ngrok
        
        # Open a HTTP tunnel on the default port 8000
        public_url = ngrok.connect(8000).public_url
        print("\n" + "="*50)
        print("✅ ТУННЕЛЬ УСПЕШНО СОЗДАН!")
        print(f"Ваш HTTPS адрес для WebApp: {public_url}")
        print("Скопируйте этот адрес и вставьте в .env файл как WEBAPP_URL")
        print("="*50 + "\n")
        
        print("Для остановки сервера нажмите Ctrl+C")
        # Keep the process alive
        while True:
            time.sleep(1)
            
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    start_tunnel()
