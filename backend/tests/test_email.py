import os
import smtplib
from email.message import EmailMessage

import pytest

@pytest.mark.skipif(
    not os.getenv('RUN_SMTP_TEST'),
    reason="Sends a real email via SMTP; set RUN_SMTP_TEST=1 to run it intentionally",
)
def test_email():
    # Leer variables de entorno (Docker las inyecta automáticamente)
    host = os.getenv('SMTP_HOST')
    port = int(os.getenv('SMTP_PORT', '587'))
    username = os.getenv('SMTP_USERNAME')
    password = os.getenv('SMTP_PASSWORD')
    sender = os.getenv('SMTP_FROM')
    
    # ⚠️ CAMBIA ESTO por el correo donde quieres recibir la prueba
    receiver = "SinLegendariasNoMola@gmail.com"

    print("Configuración cargada:")
    print(f"- Host: {host}:{port}")
    print(f"- Usuario: {username}")
    print(f"- Remitente: {sender}")
    print("Conectando con Brevo...\n")

    msg = EmailMessage()
    msg['Subject'] = 'Prueba de conexión SMTP - Sustitubot 🤖'
    msg['From'] = sender
    msg['To'] = receiver
    msg.set_content(
        '¡Hola!\n\n'
        'Si estás leyendo este mensaje, significa que las credenciales de Brevo '
        'están bien configuradas y tu aplicación de Sustitubot ya puede enviar correos.'
    )

    try:
        # Conexión usando TLS (como lo tienes en tu código principal)
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)
        print("✅ ÉXITO: El correo de prueba se ha enviado correctamente.")
    except Exception as e:
        print(f"❌ ERROR: Falló el envío del correo.")
        print(f"Detalle del error: {e}")

if __name__ == "__main__":
    test_email()