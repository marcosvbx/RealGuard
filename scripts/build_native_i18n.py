"""Compartilha as traduções do dashboard com a janela nativa do Windows."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
SOURCES = [
    'Controle de acesso', 'Modo de execução', 'RealGuard', 'Homologação',
    'Fonte de vídeo', 'Webcam do computador', 'Câmera IP (RTSP)',
    'Automática · IP ou webcam', 'Índice da webcam', 'IP da câmera',
    'Porta RTSP', 'Caminho RTSP', 'Usuário da câmera', 'Senha da câmera',
    'Deixe vazio para manter a atual', 'Testar câmera',
    'Conexão com a internet', 'Direta · sem proxy', 'Proxy do notebook mestre',
    'IP do proxy', 'Porta do proxy', 'Usuário do proxy', 'Senha do proxy',
    'Testar internet', 'Primeiro administrador', 'Usuário', 'Nome', 'Senha',
    'Salvar e iniciar', 'Salvar e reiniciar',
    'Carregando configurações…', 'Testando conexão…', 'Salvando configurações…',
    'Reiniciando o RealGuard…', 'Iniciando o RealGuard…',
    'O RealGuard não iniciou. Consulte logs/console.log.',
    'Imagem recebida da câmera.', 'Não foi possível ler a câmera.',
    'Internet disponível pelo proxy.',
]
EXTRA = {
    'RealGuard': ['RealGuard'] * 9,
    'Homologação': ['Evaluation', 'Homologación', 'Validation', 'Abnahme',
                    'التقييم', 'सत्यापन', '验证', '検証', 'Проверка'],
    'Conexão com a internet': ['Internet connection', 'Conexión a internet',
        'Connexion Internet', 'Internetverbindung', 'الاتصال بالإنترنت',
        'इंटरनेट कनेक्शन', '互联网连接', 'インターネット接続', 'Подключение к интернету'],
    'Carregando configurações…': ['Loading settings…', 'Cargando configuración…',
        'Chargement des paramètres…', 'Einstellungen werden geladen…',
        'جارٍ تحميل الإعدادات…', 'सेटिंग लोड हो रही हैं…', '正在加载设置…',
        '設定を読み込み中…', 'Загрузка настроек…'],
    'Testando conexão…': ['Testing connection…', 'Probando conexión…',
        'Test de la connexion…', 'Verbindung wird getestet…',
        'جارٍ اختبار الاتصال…', 'कनेक्शन जाँचा जा रहा है…', '正在测试连接…',
        '接続をテスト中…', 'Проверка подключения…'],
    'Salvando configurações…': ['Saving settings…', 'Guardando configuración…',
        'Enregistrement des paramètres…', 'Einstellungen werden gespeichert…',
        'جارٍ حفظ الإعدادات…', 'सेटिंग सहेजी जा रही हैं…', '正在保存设置…',
        '設定を保存中…', 'Сохранение настроек…'],
    'Reiniciando o RealGuard…': ['Restarting RealGuard…', 'Reiniciando RealGuard…',
        'Redémarrage de RealGuard…', 'RealGuard wird neu gestartet…',
        'جارٍ إعادة تشغيل RealGuard…', 'RealGuard पुनः शुरू हो रहा है…',
        '正在重启 RealGuard…', 'RealGuard を再起動中…', 'Перезапуск RealGuard…'],
    'Iniciando o RealGuard…': ['Starting RealGuard…', 'Iniciando RealGuard…',
        'Démarrage de RealGuard…', 'RealGuard wird gestartet…',
        'جارٍ تشغيل RealGuard…', 'RealGuard शुरू हो रहा है…',
        '正在启动 RealGuard…', 'RealGuard を起動中…', 'Запуск RealGuard…'],
    'O RealGuard não iniciou. Consulte logs/console.log.': [
        'RealGuard did not start. See logs/console.log.',
        'RealGuard no se inició. Consulte logs/console.log.',
        'RealGuard n’a pas démarré. Consultez logs/console.log.',
        'RealGuard wurde nicht gestartet. Siehe logs/console.log.',
        'لم يبدأ RealGuard. راجع logs/console.log.',
        'RealGuard शुरू नहीं हुआ। logs/console.log देखें।',
        'RealGuard 未启动。请查看 logs/console.log。',
        'RealGuard を起動できませんでした。logs/console.log を確認してください。',
        'RealGuard не запустился. См. logs/console.log.'
    ],
    'Imagem recebida da câmera.': ['Image received from camera.',
        'Imagen recibida de la cámara.', 'Image reçue de la caméra.',
        'Bild von der Kamera empfangen.', 'تم استلام صورة من الكاميرا.',
        'कैमरे से छवि प्राप्त हुई।', '已收到摄像头图像。',
        'カメラから画像を受信しました。', 'Изображение с камеры получено.'],
    'Não foi possível ler a câmera.': ['Could not read the camera.',
        'No se pudo leer la cámara.', 'Impossible de lire la caméra.',
        'Kamera konnte nicht gelesen werden.', 'تعذرت قراءة الكاميرا.',
        'कैमरा पढ़ा नहीं जा सका।', '无法读取摄像头。',
        'カメラの映像を読み取れませんでした。', 'Не удалось прочитать камеру.'],
    'Internet disponível pelo proxy.': ['Internet is available through the proxy.',
        'Internet disponible mediante el proxy.', 'Internet est accessible via le proxy.',
        'Internet ist über den Proxy verfügbar.', 'الإنترنت متاح عبر الوكيل.',
        'प्रॉक्सी के माध्यम से इंटरनेट उपलब्ध है।', '可通过代理访问互联网。',
        'プロキシ経由でインターネットに接続できます。', 'Интернет доступен через прокси.'],
}
rows = {}
for line in (WEB / 'i18n.js').read_text(encoding='utf-8').splitlines():
    match = re.match(r"\s*'([^']+)':(\[.*\]),?\s*$", line)
    if match and match.group(1) in SOURCES:
        rows[match.group(1)] = ast.literal_eval(match.group(2))
rows.update(EXTRA)
missing = [key for key in SOURCES if key not in rows]
if missing:
    raise SystemExit('Traduções ausentes: ' + ', '.join(missing))
if any(len(rows[key]) != 9 for key in SOURCES):
    raise SystemExit('Cada item precisa de nove traduções.')
path = WEB / 'native-config-i18n.tsv'
path.write_text(''.join('\t'.join([key] + rows[key]) + '\n' for key in SOURCES), encoding='utf-8')
print(f'{len(SOURCES)} textos gerados em {path.name}')
