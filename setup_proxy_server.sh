#!/bin/bash
# Запуск от имени root (sudo)

# Замените эти две переменные на ВАШИ данные:
# ДОМЕН, который направлен на этот сервер (89.110.94.180)
DOMAIN="subs.guepardvpn.top"

# РЕАЛЬНЫЙ IP ОСНОВНОГО СЕРВЕРА (где запущен бот)
MAIN_SERVER_IP="213.165.37.219"

# 1. Загрузка и установка зависимостей
echo "Обновление пакетов и установка Nginx/Certbot..."
apt update
apt install -y nginx certbot python3-certbot-nginx

# 2. Создание конфига Nginx
echo "Настройка конфигурации Nginx..."

cat > /etc/nginx/sites-available/$DOMAIN <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://$MAIN_SERVER_IP:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # Переброс важных заголовков для проверки лимита (3 устройств)
        proxy_set_header User-Agent \$http_user_agent;
        proxy_set_header X-Device-Id \$http_x_device_id;
        proxy_set_header X-Device-Model \$http_x_device_model;
        proxy_set_header X-Device-Os \$http_x_device_os;
    }
}
EOF

# 3. Активация сайта
ln -s /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/
rm /etc/nginx/sites-enabled/default

# 4. Проверка и перезагрузка
nginx -t
systemctl restart nginx

# 5. Установка SSL-сертификата (сработает ТОЛЬКО если домен уже привязан к этому серверу)
echo "Установка SSL сертификата Let's Encrypt..."
certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m admin@$DOMAIN

echo "✅ ГОТОВО! Прокси сервер настроен."
echo "Убедитесь что в .env основного сервера 'PROXY_URL_BASE' установлен как 'https://$DOMAIN'"
