/* Interface local: funciona sem internet e não altera os valores enviados à API. */
(() => {
  'use strict';
  const codes = ['pt-BR','en','es','fr','de','ar','hi','zh-CN','ja','ru'];
  const flagCountryCodes = ['br','us','es','fr','de','sa','in','cn','jp','ru'];
  const names = ['Português (Brasil)','English','Español','Français','Deutsch','العربية','हिन्दी','简体中文','日本語','Русский'];
  const flagArtwork = {
    br: '<rect width="64" height="40" fill="#009739"/><path d="M32 4 59 20 32 36 5 20Z" fill="#ffdf00"/><circle cx="32" cy="20" r="10.5" fill="#002776"/><path d="M22 17q10-4 20 3" fill="none" stroke="#fff" stroke-width="2"/>',
    us: Array.from({length:13}, (_, i) => `<rect y="${i * 40 / 13}" width="64" height="${40 / 13}" fill="${i % 2 ? '#fff' : '#b22234'}"/>`).join('') + '<rect width="26" height="21.54" fill="#3c3b6e"/>' + Array.from({length:5}, (_, row) => Array.from({length:6}, (_, col) => `<circle cx="${2.5 + col * 4.2}" cy="${2.2 + row * 4.2}" r=".9" fill="#fff"/>`).join('')).join(''),
    es: '<rect width="64" height="40" fill="#aa151b"/><rect y="10" width="64" height="20" fill="#f1bf00"/><path d="M19 16h9v9h-9z" fill="#aa151b"/><path d="M21 14h5v2h-5z" fill="#aa151b"/><path d="M21 18h5v5h-5z" fill="#f1bf00"/>',
    fr: '<rect width="64" height="40" fill="#ed2939"/><rect width="42.67" height="40" fill="#fff"/><rect width="21.33" height="40" fill="#002395"/>',
    de: '<rect width="64" height="40" fill="#ffce00"/><rect width="64" height="26.67" fill="#dd0000"/><rect width="64" height="13.33" fill="#000"/>',
    sa: '<rect width="64" height="40" fill="#006c35"/><text x="32" y="20" text-anchor="middle" fill="#fff" font-family="serif" font-size="7" font-weight="bold">لا إله إلا الله محمد رسول الله</text><path d="M14 28h35q4 0 5-2" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/><path d="M14 25v4" stroke="#fff" stroke-width="1.8"/>',
    in: '<rect width="64" height="40" fill="#138808"/><rect width="64" height="26.67" fill="#fff"/><rect width="64" height="13.33" fill="#ff9933"/><circle cx="32" cy="20" r="5" fill="none" stroke="#000080" stroke-width="1"/>' + Array.from({length:12}, (_, i) => `<path d="M32 20v-4" stroke="#000080" stroke-width=".45" transform="rotate(${i * 30} 32 20)"/>`).join(''),
    cn: '<rect width="64" height="40" fill="#de2910"/><polygon points="12,5 13.4,9.2 17.8,9.2 14.2,11.8 15.6,16 12,13.4 8.4,16 9.8,11.8 6.2,9.2 10.6,9.2" fill="#ffde00"/><circle cx="22" cy="6" r="1.5" fill="#ffde00"/><circle cx="26" cy="10" r="1.5" fill="#ffde00"/><circle cx="26" cy="16" r="1.5" fill="#ffde00"/><circle cx="22" cy="20" r="1.5" fill="#ffde00"/>',
    jp: '<rect width="64" height="40" fill="#fff"/><circle cx="32" cy="20" r="12" fill="#bc002d"/>',
    ru: '<rect width="64" height="40" fill="#d52b1e"/><rect width="64" height="26.67" fill="#0039a6"/><rect width="64" height="13.33" fill="#fff"/>'
  };
  const flagSources = flagCountryCodes.map(code => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 40">' + flagArtwork[code] + '</svg>'));
  // A ordem das traduções é: inglês, espanhol, francês, alemão, árabe, hindi, chinês, japonês, russo.
  const rows = {
    'Proteção real, em tempo real':['Real protection, in real time','Protección real, en tiempo real','Protection réelle, en temps réel','Echter Schutz in Echtzeit','حماية حقيقية في الوقت الفعلي','वास्तविक समय में वास्तविक सुरक्षा','实时守护，真正安心','リアルタイムで確かな保護','Надёжная защита в реальном времени'],
    'Controle de acesso':['Access control','Control de acceso','Contrôle d’accès','Zugangskontrolle','التحكم في الدخول','प्रवेश नियंत्रण','门禁管理','入退室管理','Контроль доступа'],
    'Operação':['Operations','Operación','Opérations','Betrieb','التشغيل','संचालन','运行','運用','Работа'],
    'Gestão':['Management','Gestión','Gestion','Verwaltung','الإدارة','प्रबंधन','管理','管理','Управление'],
    'Sistema':['System','Sistema','Système','System','النظام','सिस्टम','系统','システム','Система'],
    'Visão geral':['Overview','Vista general','Vue d’ensemble','Übersicht','نظرة عامة','अवलोकन','概览','概要','Обзор'],
    'Câmera':['Camera','Cámara','Caméra','Kamera','كاميرا','कैमरा','摄像头','カメラ','Камера'],
    'Câmeras':['Cameras','Cámaras','Caméras','Kameras','الكاميرات','कैमरे','摄像头','カメラ','Камеры'],
    'Turma':['Class','Clase','Classe','Klasse','صف','कक्षा','班级','クラス','Класс'],
    'Aluno':['Student','Alumno','Élève','Schüler','طالب','छात्र','学生','生徒','Ученик'],
    'Status':['Status','Estado','Statut','Status','الحالة','स्थिति','状态','状態','Статус'],
    'Horário':['Time','Horario','Heure','Uhrzeit','الوقت','समय','时间','時刻','Время'],
    'Hoje':['Today','Hoy','Aujourd’hui','Heute','اليوم','आज','今天','今日','Сегодня'],
    'agora':['now','ahora','maintenant','jetzt','الآن','अभी','现在','現在','сейчас'],
    'ao vivo':['live','en vivo','en direct','live','مباشر','लाइव','实时','ライブ','в эфире'],
    'Ver ao vivo':['View live','Ver en vivo','Voir en direct','Live ansehen','عرض مباشر','लाइव देखें','查看实时画面','ライブを見る','Смотреть трансляцию'],
    'Outra janela':['Other window','Otra ventana','Autre fenêtre','Anderes Fenster','نافذة أخرى','दूसरी विंडो','其他窗口','別ウィンドウ','Другое окно'],
    'Gerenciador':['Manager','Gestor','Gestionnaire','Verwaltung','المدير','प्रबंधक','管理器','管理','Управление'],
    'Detalhes':['Details','Detalles','Détails','Details','التفاصيل','विवरण','详情','詳細','Подробности'],
    'detalhes':['details','detalles','détails','Details','التفاصيل','विवरण','详情','詳細','подробности'],
    'Auditoria':['Audit','Auditoría','Audit','Prüfung','التدقيق','ऑडिट','审计','監査','Аудит'],
    'Usuários':['Users','Usuarios','Utilisateurs','Benutzer','المستخدمون','उपयोगकर्ता','用户','ユーザー','Пользователи'],
    'Perfis de acesso':['Access roles','Perfiles de acceso','Profils d’accès','Zugriffsrollen','أدوار الوصول','प्रवेश भूमिकाएँ','访问角色','アクセス権限','Роли доступа'],
    'Backup':['Backup','Copia de seguridad','Sauvegarde','Sicherung','نسخة احتياطية','बैकअप','备份','バックアップ','Резервная копия'],
    'Dados fictícios':['Sample data','Datos ficticios','Données fictives','Beispieldaten','بيانات تجريبية','नमूना डेटा','示例数据','サンプルデータ','Тестовые данные'],
    'Data e hora da demonstração':['Demo date and time','Fecha y hora de la demostración','Date et heure de la démonstration','Datum und Uhrzeit der Vorführung','تاريخ ووقت العرض التجريبي','डेमो की तारीख और समय','演示日期和时间','デモの日付と時刻','Дата и время демонстрации'],
    'Voltar à data e hora reais':['Return to actual date and time','Volver a la fecha y hora reales','Revenir à la date et à l’heure réelles','Zum tatsächlichen Datum und zur Uhrzeit zurückkehren','العودة إلى التاريخ والوقت الفعليين','वास्तविक तारीख और समय पर लौटें','恢复实际日期和时间','実際の日付と時刻に戻す','Вернуться к реальной дате и времени'],
    'Data':['Date','Fecha','Date','Datum','التاريخ','तारीख','日期','日付','Дата'],
    'Hora':['Time','Hora','Heure','Uhrzeit','الوقت','समय','时间','時刻','Время'],
    'Planilha da base':['Database spreadsheet','Hoja de la base','Tableur de la base','Datenbanktabelle','جدول قاعدة البيانات','डेटाबेस शीट','数据库表格','データ一覧','Таблица базы'],
    'Regras e retenção':['Rules and retention','Reglas y conservación','Règles et conservation','Regeln und Aufbewahrung','القواعد والاحتفاظ','नियम और प्रतिधारण','规则与保留期限','規則と保存期間','Правила и хранение'],
    'Termo de consentimento':['Consent form','Documento de consentimiento','Formulaire de consentement','Einwilligungserklärung','نموذج الموافقة','सहमति प्रपत्र','同意书','同意書','Форма согласия'],
    'Frequência':['Attendance rate','Asistencia','Assiduité','Anwesenheitsquote','معدل الحضور','उपस्थिति दर','出勤率','出席率','Посещаемость'],
    'Faltas':['Absences','Faltas','Absences','Fehlzeiten','الغياب','अनुपस्थिति','缺勤','欠席','Пропуски'],
    'Saída':['Exit','Salida','Sortie','Ausgang','خروج','निकास','离开','退出','Выход'],
    'Entrada':['Entry','Entrada','Entrée','Eingang','دخول','प्रवेश','进入','入室','Вход'],
    'Fotos':['Photos','Fotos','Photos','Fotos','صور','फ़ोटो','照片','写真','Фото'],
    'Poucas fotos':['Few photos','Pocas fotos','Peu de photos','Wenige Fotos','صور قليلة','कम फ़ोटो','照片不足','写真が少ない','Мало фото'],
    'Sem termo':['No consent','Sin consentimiento','Sans consentement','Keine Einwilligung','بلا موافقة','सहमति नहीं','无同意书','同意書なし','Нет согласия'],
    'Responsáveis':['Guardians','Responsables','Responsables','Erziehungsberechtigte','أولياء الأمور','अभिभावक','监护人','保護者','Опекуны'],
    'Responsável':['Guardian','Responsable','Responsable','Erziehungsberechtigter','ولي الأمر','अभिभावक','监护人','保護者','Опекун'],
    'Integrações':['Integrations','Integraciones','Intégrations','Integrationen','التكاملات','एकीकरण','集成','連携','Интеграции'],
    'Assistente virtual':['Virtual assistant','Asistente virtual','Assistant virtuel','Virtueller Assistent','مساعد افتراضي','वर्चुअल सहायक','虚拟助手','仮想アシスタント','Виртуальный помощник'],
    'Vincular Telegram':['Link Telegram','Vincular Telegram','Lier Telegram','Telegram verknüpfen','ربط تيليغرام','टेलीग्राम लिंक करें','绑定 Telegram','Telegramを連携','Подключить Telegram'],
    'Sem Telegram vinculado':['Telegram not linked','Telegram sin vincular','Telegram non lié','Telegram nicht verknüpft','تيليغرام غير مرتبط','टेलीग्राम लिंक नहीं है','未绑定 Telegram','Telegram未連携','Telegram не подключён'],
    'Telegram pendente':['Telegram pending','Telegram pendiente','Telegram en attente','Telegram ausstehend','تيليغرام معلق','टेलीग्राम लंबित','Telegram 待处理','Telegram保留中','Telegram ожидает'],
    'Ocorrências':['Incidents','Incidencias','Incidents','Vorfälle','الحوادث','घटनाएँ','事件','インシデント','События'],
    'Alertas de segurança, retiradas do dia, avisos que falharam e histórico':['Security alerts, today’s pickups, failed notifications and history','Alertas de seguridad, recogidas de hoy, avisos fallidos e historial','Alertes de sécurité, retraits du jour, avis échoués et historique','Sicherheitswarnungen, heutige Abholungen, fehlgeschlagene Mitteilungen und Verlauf','تنبيهات الأمن واستلامات اليوم والإشعارات الفاشلة والسجل','सुरक्षा अलर्ट, आज के पिकअप, विफल सूचनाएँ और इतिहास','安保警报、今日接走、失败通知及历史记录','セキュリティ警告、今日の引き取り、通知失敗、履歴','Оповещения безопасности, сегодняшние заборы, сбои уведомлений и история'],
    'Câmeras e reconhecimento em tempo real':['Cameras and real-time recognition','Cámaras y reconocimiento en tiempo real','Caméras et reconnaissance en temps réel','Kameras und Erkennung in Echtzeit','الكاميرات والتعرف في الوقت الفعلي','कैमरे और वास्तविक समय में पहचान','摄像头与实时识别','カメラとリアルタイム認識','Камеры и распознавание в реальном времени'],
    'Presença, pontualidade e movimento':['Attendance, punctuality and activity','Asistencia, puntualidad y movimiento','Présence, ponctualité et activité','Anwesenheit, Pünktlichkeit und Bewegung','الحضور والالتزام بالوقت والحركة','उपस्थिति, समयपालन और गतिविधि','出勤、准时与动态','出席、時間厳守、動き','Посещаемость, пунктуальность и движение'],
    'Cadastro, responsáveis e reconhecimento facial':['Registration, guardians and face recognition','Registro, responsables y reconocimiento facial','Inscriptions, responsables et reconnaissance faciale','Registrierung, Erziehungsberechtigte und Gesichtserkennung','التسجيل وأولياء الأمور والتعرف على الوجه','पंजीकरण, अभिभावक और चेहरा पहचान','登记、监护人与人脸识别','登録、保護者、顔認証','Регистрация, опекуны и распознавание лиц'],
    'Usuários, perfis de acesso, auditoria, backup e dados fictícios':['Users, access roles, audit, backup and sample data','Usuarios, perfiles de acceso, auditoría, copias y datos ficticios','Utilisateurs, profils d’accès, audit, sauvegarde et données fictives','Benutzer, Zugriffsrollen, Prüfung, Sicherung und Beispieldaten','المستخدمون والأدوار والتدقيق والنسخ الاحتياطي والبيانات التجريبية','उपयोगकर्ता, प्रवेश भूमिकाएँ, ऑडिट, बैकअप और नमूना डेटा','用户、权限、审计、备份与示例数据','ユーザー、権限、監査、バックアップ、サンプルデータ','Пользователи, роли, аудит, резервные копии и тестовые данные'],
    'Turnos, turmas, calendário letivo, regras de presença e integrações':['Shifts, classes, school calendar, attendance rules and integrations','Turnos, clases, calendario escolar, reglas de asistencia e integraciones','Périodes, classes, calendrier scolaire, règles de présence et intégrations','Schichten, Klassen, Schulkalender, Anwesenheitsregeln und Integrationen','الفترات والصفوف والتقويم الدراسي وقواعد الحضور والتكاملات','शिफ्ट, कक्षाएँ, स्कूल कैलेंडर, उपस्थिति नियम और एकीकरण','时段、班级、校历、出勤规则与集成','時間帯、クラス、学校暦、出席規則、連携','Смены, классы, школьный календарь, правила посещаемости и интеграции'],
    'Ao vivo':['Live','En vivo','En direct','Live','مباشر','लाइव','实时画面','ライブ','Прямой эфир'],
    'AO VIVO':['LIVE','EN VIVO','EN DIRECT','LIVE','مباشر','लाइव','实时','ライブ','В ЭФИРЕ'],
    'Relatórios':['Reports','Informes','Rapports','Berichte','التقارير','रिपोर्ट','报表','レポート','Отчёты'],
    'Alunos':['Students','Alumnos','Élèves','Schüler','الطلاب','छात्र','学生','生徒','Ученики'],
    'Escola':['School','Escuela','École','Schule','المدرسة','विद्यालय','学校','学校','Школа'],
    'Administração':['Administration','Administración','Administration','Verwaltung','الإدارة','प्रशासन','管理后台','管理','Администрирование'],
    'Estado do sistema':['System status','Estado del sistema','État du système','Systemstatus','حالة النظام','सिस्टम स्थिति','系统状态','システム状態','Состояние системы'],
    'Operando':['Running','Operando','En service','In Betrieb','قيد التشغيل','चालू','运行中','稼働中','Работает'],
    'Conectando…':['Connecting…','Conectando…','Connexion…','Verbinde…','جارٍ الاتصال…','कनेक्ट हो रहा है…','连接中…','接続中…','Подключение…'],
    'conectando…':['connecting…','conectando…','connexion…','verbinde…','جارٍ الاتصال…','कनेक्ट हो रहा है…','连接中…','接続中…','подключение…'],
    'Demonstração':['Demo','Demostración','Démonstration','Demo','عرض تجريبي','डेमो','演示','デモ','Демонстрация'],
    'Entrar':['Sign in','Entrar','Se connecter','Anmelden','تسجيل الدخول','साइन इन','登录','ログイン','Войти'],
    'Use o usuário criado pelo administrador.':['Use the account created by the administrator.','Usa la cuenta creada por el administrador.','Utilisez le compte créé par l’administrateur.','Verwenden Sie das vom Administrator erstellte Konto.','استخدم الحساب الذي أنشأه المسؤول.','प्रशासक द्वारा बनाया गया खाता उपयोग करें।','请使用管理员创建的账户。','管理者が作成したアカウントを使用してください。','Используйте учётную запись, созданную администратором.'],
    'Nenhum administrador cadastrado ainda. No terminal da máquina do RealGuard, rode':['No administrator has been registered. On the RealGuard computer, run','Todavía no hay administrador registrado. En el ordenador de RealGuard, ejecuta','Aucun administrateur inscrit. Sur l’ordinateur RealGuard, exécutez','Noch kein Administrator registriert. Führen Sie auf dem RealGuard-Computer','لم يُسجَّل مسؤول بعد. على جهاز RealGuard، شغّل','अभी कोई प्रशासक पंजीकृत नहीं है। RealGuard कंप्यूटर पर चलाएँ','尚未注册管理员。请在 RealGuard 设备的终端运行','管理者がまだ登録されていません。RealGuard端末で次を実行してください','Администратор ещё не зарегистрирован. На компьютере RealGuard выполните'],
    'e depois entre aqui.':['and then sign in here.','y después entra aquí.','puis connectez-vous ici.','aus und melden Sie sich dann hier an.','ثم سجّل الدخول هنا.','फिर यहाँ साइन इन करें।','然后在此登录。','その後、ここでログインしてください。','затем войдите здесь.'],
    'Você entrou com uma senha provisória. Defina uma senha só sua para continuar.':['You signed in with a temporary password. Set your own password to continue.','Has entrado con una contraseña provisional. Define una contraseña propia para continuar.','Vous utilisez un mot de passe provisoire. Définissez le vôtre pour continuer.','Sie haben sich mit einem vorläufigen Passwort angemeldet. Legen Sie ein eigenes fest.','دخلت بكلمة مرور مؤقتة. أنشئ كلمة مرور خاصة بك للمتابعة.','आपने अस्थायी पासवर्ड से साइन इन किया है। आगे बढ़ने के लिए अपना पासवर्ड बनाएँ।','您使用临时密码登录。请设置自己的密码以继续。','仮パスワードでログインしました。続行するには新しいパスワードを設定してください。','Вы вошли с временным паролем. Создайте свой пароль, чтобы продолжить.'],
    '(mínimo 8 caracteres)':['(at least 8 characters)','(mínimo 8 caracteres)','(8 caractères minimum)','(mindestens 8 Zeichen)','(8 أحرف على الأقل)','(कम से कम 8 अक्षर)','（至少 8 个字符）','（8文字以上）','(не менее 8 символов)'],
    'Conexão sem criptografia (http) — adequada só na rede local do laboratório.':['Unencrypted connection (http) — use only on the local lab network.','Conexión sin cifrar (http): úsala solo en la red local del laboratorio.','Connexion non chiffrée (http) — à utiliser uniquement sur le réseau local du laboratoire.','Unverschlüsselte Verbindung (http) – nur im lokalen Labornetz verwenden.','اتصال غير مشفر (http) — استخدمه فقط على الشبكة المحلية للمختبر.','बिना एन्क्रिप्शन वाला कनेक्शन (http) — केवल स्थानीय लैब नेटवर्क पर उपयोग करें।','未加密连接（http）—仅适用于实验室局域网。','暗号化されていない接続（http）—研究室のローカルネットワークでのみ使用してください。','Незашифрованное соединение (http) — используйте только в локальной сети лаборатории.'],
    'A IA percebe, o sistema interpreta, o humano valida.':['AI detects, the system interprets, a person decides.','La IA detecta, el sistema interpreta y una persona valida.','L’IA détecte, le système interprète, l’humain valide.','KI erkennt, das System interpretiert, ein Mensch bestätigt.','يرصد الذكاء الاصطناعي، ويفسر النظام، ويتحقق الإنسان.','एआई पहचानता है, सिस्टम समझता है, इंसान सत्यापित करता है।','AI 负责感知，系统负责分析，人工负责确认。','AIが検知し、システムが解釈し、人が確認します。','ИИ обнаруживает, система анализирует, человек подтверждает.'],
    'As senhas não conferem.':['Passwords do not match.','Las contraseñas no coinciden.','Les mots de passe ne correspondent pas.','Die Passwörter stimmen nicht überein.','كلمتا المرور غير متطابقتين.','पासवर्ड मेल नहीं खाते।','两次输入的密码不一致。','パスワードが一致しません。','Пароли не совпадают.'],
    'Dados inválidos.':['Invalid data.','Datos no válidos.','Données invalides.','Ungültige Daten.','بيانات غير صالحة.','अमान्य जानकारी।','数据无效。','無効なデータです。','Неверные данные.'],
    'Sessão expirada. Entre novamente.':['Session expired. Sign in again.','La sesión ha caducado. Vuelve a entrar.','Session expirée. Reconnectez-vous.','Sitzung abgelaufen. Bitte erneut anmelden.','انتهت الجلسة. سجّل الدخول مرة أخرى.','सत्र समाप्त हुआ। फिर साइन इन करें।','会话已过期，请重新登录。','セッションの有効期限が切れました。再度ログインしてください。','Сессия истекла. Войдите снова.'],
    'Usuário':['Username','Usuario','Utilisateur','Benutzername','اسم المستخدم','उपयोगकर्ता','用户名','ユーザー名','Имя пользователя'],
    'Senha':['Password','Contraseña','Mot de passe','Passwort','كلمة المرور','पासवर्ड','密码','パスワード','Пароль'],
    'Mostrar senha':['Show password','Mostrar contraseña','Afficher le mot de passe','Passwort anzeigen','إظهار كلمة المرور','पासवर्ड दिखाएँ','显示密码','パスワードを表示','Показать пароль'],
    'Ocultar senha':['Hide password','Ocultar contraseña','Masquer le mot de passe','Passwort verbergen','إخفاء كلمة المرور','पासवर्ड छिपाएँ','隐藏密码','パスワードを隠す','Скрыть пароль'],
    'Crie sua senha':['Create your password','Crea tu contraseña','Créez votre mot de passe','Passwort erstellen','أنشئ كلمة مرور','अपना पासवर्ड बनाएँ','创建密码','パスワードを作成','Создайте пароль'],
    'Senha provisória':['Temporary password','Contraseña provisional','Mot de passe provisoire','Vorläufiges Passwort','كلمة مرور مؤقتة','अस्थायी पासवर्ड','临时密码','仮パスワード','Временный пароль'],
    'Nova senha':['New password','Nueva contraseña','Nouveau mot de passe','Neues Passwort','كلمة مرور جديدة','नया पासवर्ड','新密码','新しいパスワード','Новый пароль'],
    'Repita a nova senha':['Repeat new password','Repite la nueva contraseña','Répétez le nouveau mot de passe','Neues Passwort wiederholen','أعد إدخال كلمة المرور الجديدة','नया पासवर्ड दोहराएँ','重复新密码','新しいパスワードを再入力','Повторите новый пароль'],
    'Salvar e entrar':['Save and sign in','Guardar y entrar','Enregistrer et se connecter','Speichern und anmelden','حفظ وتسجيل الدخول','सहेजें और साइन इन करें','保存并登录','保存してログイン','Сохранить и войти'],
    'Sair':['Sign out','Salir','Se déconnecter','Abmelden','تسجيل الخروج','साइन आउट','退出登录','ログアウト','Выйти'],
    'Sua conta':['Your account','Tu cuenta','Votre compte','Ihr Konto','حسابك','आपका खाता','您的账户','アカウント','Ваш аккаунт'],
    'Trocar minha senha':['Change my password','Cambiar mi contraseña','Changer mon mot de passe','Passwort ändern','تغيير كلمة المرور','पासवर्ड बदलें','修改密码','パスワードを変更','Сменить пароль'],
    'Hora do equipamento':['Device time','Hora del equipo','Heure de l’appareil','Gerätezeit','وقت الجهاز','डिवाइस का समय','设备时间','端末時刻','Время устройства'],
    'Alertas para decidir':['Alerts to review','Alertas por decidir','Alertes à traiter','Offene Warnungen','تنبيهات بانتظار القرار','समीक्षा के लिए अलर्ट','待处理警报','確認待ちの警告','Ожидающие решения предупреждения'],
    'Aguardando decisão':['Awaiting review','Esperando decisión','En attente de décision','Wartet auf Entscheidung','بانتظار القرار','निर्णय की प्रतीक्षा','等待处理','判断待ち','Ожидает решения'],
    'Ver ocorrências':['View incidents','Ver incidencias','Voir les incidents','Vorfälle ansehen','عرض الحوادث','घटनाएँ देखें','查看事件','インシデントを見る','Посмотреть события'],
    'Ver histórico':['View history','Ver historial','Voir l’historique','Verlauf ansehen','عرض السجل','इतिहास देखें','查看历史','履歴を見る','Посмотреть историю'],
    'Histórico de alertas':['Alert history','Historial de alertas','Historique des alertes','Warnungsverlauf','سجل التنبيهات','अलर्ट इतिहास','警报历史','警告履歴','История предупреждений'],
    'Nenhuma ocorrência pendente':['No pending incidents','Ninguna incidencia pendiente','Aucun incident en attente','Keine offenen Vorfälle','لا توجد حوادث معلقة','कोई लंबित घटना नहीं','没有待处理事件','保留中のインシデントはありません','Нет ожидающих событий'],
    'Nenhuma decisão nesta sessão':['No decisions in this session','Ninguna decisión en esta sesión','Aucune décision pendant cette session','Keine Entscheidungen in dieser Sitzung','لا قرارات في هذه الجلسة','इस सत्र में कोई निर्णय नहीं','本次会话尚无处理记录','このセッションでの判断はありません','Нет решений в этой сессии'],
    'Confirmar':['Confirm','Confirmar','Confirmer','Bestätigen','تأكيد','पुष्टि करें','确认','確認','Подтвердить'],
    'Descartar':['Dismiss','Descartar','Écarter','Verwerfen','تجاهل','खारिज करें','忽略','破棄','Отклонить'],
    'Pendente':['Pending','Pendiente','En attente','Ausstehend','معلق','लंबित','待处理','保留中','Ожидает'],
    'Resolvido':['Resolved','Resuelto','Résolu','Erledigt','تم الحل','सुलझाया गया','已解决','解決済み','Решено'],
    'Pessoa não identificada':['Unidentified person','Persona no identificada','Personne non identifiée','Nicht identifizierte Person','شخص غير معروف','अज्ञात व्यक्ति','未识别人员','未識別の人物','Неопознанный человек'],
    'Rosto obstruído':['Obstructed face','Rostro obstruido','Visage masqué','Verdecktes Gesicht','وجه محجوب','ढका हुआ चेहरा','面部遮挡','顔が隠れています','Лицо закрыто'],
    'Rostos obstruídos':['Obstructed faces','Rostros obstruidos','Visages masqués','Verdeckte Gesichter','وجوه محجوبة','ढके हुए चेहरे','面部遮挡','顔の遮蔽','Закрытые лица'],
    'Não identificado':['Unidentified','No identificado','Non identifié','Nicht identifiziert','غير معروف','अज्ञात','未识别','未識別','Не опознан'],
    'Não cadastradas':['Unregistered','No registradas','Non inscrites','Nicht registriert','غير مسجلين','अपंजीकृत','未登记','未登録','Не зарегистрированы'],
    'Identificadas':['Identified','Identificadas','Identifiées','Identifiziert','تم التعرف عليهم','पहचाने गए','已识别','識別済み','Опознаны'],
    'Pessoas na sala':['People in the room','Personas en el aula','Personnes dans la salle','Personen im Raum','أشخاص في القاعة','कक्षा में लोग','教室内人数','教室内の人数','Людей в классе'],
    'Presença hoje':['Attendance today','Asistencia hoy','Présences aujourd’hui','Anwesenheit heute','الحضور اليوم','आज की उपस्थिति','今日出勤','今日の出席','Посещаемость сегодня'],
    'Na escola agora':['At school now','En la escuela ahora','À l’école maintenant','Jetzt in der Schule','في المدرسة الآن','अभी स्कूल में','当前在校','現在学校内','Сейчас в школе'],
    'Ausências':['Absences','Ausencias','Absences','Fehlzeiten','الغياب','अनुपस्थिति','缺勤','欠席','Отсутствуют'],
    'Atrasos':['Late arrivals','Retrasos','Retards','Verspätungen','التأخر','देरी','迟到','遅刻','Опоздания'],
    'Chegadas por hora':['Arrivals by hour','Llegadas por hora','Arrivées par heure','Ankünfte pro Stunde','الوصول حسب الساعة','घंटे के अनुसार आगमन','每小时到达人数','時間別の到着数','Приходы по часам'],
    'Chegadas por dia':['Arrivals by day','Llegadas por día','Arrivées par jour','Ankünfte pro Tag','الوصول حسب اليوم','दिन के अनुसार आगमन','每日到达人数','日別の到着数','Приходы по дням'],
    'Movimento por horário':['Activity by hour','Movimiento por horario','Activité par heure','Aktivität nach Uhrzeit','الحركة حسب الساعة','समय के अनुसार गतिविधि','每小时活动','時間帯別の動き','Движение по часам'],
    'Alertas por horário':['Alerts by hour','Alertas por horario','Alertes par heure','Warnungen nach Uhrzeit','التنبيهات حسب الساعة','घंटे के अनुसार अलर्ट','每小时警报','時間帯別の警告','Предупреждения по часам'],
    'Por dia da semana':['By weekday','Por día de la semana','Par jour de la semaine','Nach Wochentag','حسب يوم الأسبوع','सप्ताह के दिन के अनुसार','按星期','曜日別','По дням недели'],
    'Tendência semana a semana':['Week-by-week trend','Tendencia semanal','Tendance semaine après semaine','Wöchentlicher Trend','الاتجاه الأسبوعي','साप्ताहिक रुझान','每周趋势','週ごとの傾向','Недельная динамика'],
    'Composição das presenças':['Attendance breakdown','Composición de asistencias','Répartition des présences','Anwesenheitsverteilung','توزيع الحضور','उपस्थिति विवरण','出勤构成','出席の内訳','Состав посещений'],
    'Frequência por turma':['Attendance by class','Asistencia por clase','Assiduité par classe','Anwesenheit nach Klasse','الحضور حسب الصف','कक्षा के अनुसार उपस्थिति','班级出勤率','クラス別の出席率','Посещаемость по классам'],
    'Pontualidade':['Punctuality','Puntualidad','Ponctualité','Pünktlichkeit','الالتزام بالوقت','समयपालन','准时率','時間厳守','Пунктуальность'],
    'Menor frequência':['Lowest attendance','Menor asistencia','Assiduité la plus faible','Niedrigste Anwesenheit','أقل حضور','सबसे कम उपस्थिति','最低出勤率','最低出席率','Наименьшая посещаемость'],
    'Abaixo de 75%':['Below 75%','Por debajo del 75%','Sous 75 %','Unter 75 %','أقل من 75٪','75% से कम','低于 75%','75%未満','Ниже 75%'],
    'Frequência abaixo do mínimo':['Attendance below minimum','Asistencia inferior al mínimo','Assiduité sous le minimum','Anwesenheit unter Mindestwert','حضور دون الحد الأدنى','न्यूनतम से कम उपस्थिति','出勤率低于最低要求','最低出席率未満','Посещаемость ниже минимума'],
    'Alunos com mais atrasos':['Students with most late arrivals','Alumnos con más retrasos','Élèves les plus en retard','Schüler mit den meisten Verspätungen','الطلاب الأكثر تأخراً','सबसे अधिक देर से आने वाले छात्र','迟到最多的学生','遅刻が多い生徒','Чаще всего опаздывают'],
    'Alunos com mais faltas':['Students with most absences','Alumnos con más faltas','Élèves les plus absents','Schüler mit den meisten Fehlzeiten','الطلاب الأكثر غياباً','सबसे अधिक अनुपस्थित छात्र','缺勤最多的学生','欠席が多い生徒','Больше всего пропусков'],
    'Faltas seguidas':['Consecutive absences','Faltas seguidas','Absences consécutives','Aufeinanderfolgende Fehlzeiten','غيابات متتالية','लगातार अनुपस्थिति','连续缺勤','連続欠席','Пропуски подряд'],
    '1 registro = 1 aluno em 1 dia letivo':['1 record = 1 student on 1 school day','1 registro = 1 alumno en 1 día lectivo','1 enregistrement = 1 élève sur 1 jour de classe','1 Eintrag = 1 Schüler an 1 Schultag','سجل واحد = طالب واحد في يوم دراسي واحد','1 रिकॉर्ड = 1 छात्र का 1 स्कूल दिन','1 条记录 = 1 名学生在 1 个上课日','1件の記録＝1人の生徒の1授業日','1 запись = 1 ученик за 1 учебный день'],
    '2 ou mais no período':['2 or more in the period','2 o más en el período','2 ou plus sur la période','2 oder mehr im Zeitraum','اثنان أو أكثر خلال الفترة','अवधि में 2 या अधिक','期间内 2 次以上','期間内に2回以上','2 и более за период'],
    '3 ou mais dias · busca ativa':['3 or more days · follow-up required','3 o más días · seguimiento activo','3 jours ou plus · suivi actif','3 oder mehr Tage · aktive Nachverfolgung','3 أيام أو أكثر · متابعة نشطة','3 या अधिक दिन · सक्रिय अनुवर्ती','3 天以上 · 主动跟进','3日以上・積極的な確認','3 и более дней · требуется проверка'],
    'frequência mínima (LDB)':['minimum attendance (LDB)','asistencia mínima (LDB)','assiduité minimale (LDB)','Mindestanwesenheit (LDB)','الحد الأدنى للحضور (LDB)','न्यूनतम उपस्थिति (LDB)','最低出勤率（LDB）','最低出席率（LDB）','минимальная посещаемость (LDB)'],
    'frequência e pontualidade':['attendance and punctuality','asistencia y puntualidad','assiduité et ponctualité','Anwesenheit und Pünktlichkeit','الحضور والالتزام بالوقت','उपस्थिति और समयपालन','出勤率与准时率','出席率と時間厳守','посещаемость и пунктуальность'],
    'frequência e % de chegadas com atraso':['attendance and % of late arrivals','asistencia y % de llegadas tarde','assiduité et % d’arrivées tardives','Anwesenheit und % verspäteter Ankünfte','الحضور ونسبة التأخر','उपस्थिति और देर से आने का प्रतिशत','出勤率与迟到比例','出席率と遅刻率','посещаемость и доля опозданий'],
    'abaixo de 75% · atenção até 80%':['below 75% · caution up to 80%','por debajo del 75% · atención hasta el 80%','sous 75 % · vigilance jusqu’à 80 %','unter 75 % · Achtung bis 80 %','أقل من 75٪ · تنبيه حتى 80٪','75% से कम · 80% तक सावधानी','低于 75% · 80% 以下需关注','75%未満・80%までは注意','ниже 75% · внимание до 80%'],
    'ausências sem justificativa':['unexcused absences','ausencias sin justificar','absences non justifiées','unentschuldigte Fehlzeiten','غياب غير مبرر','बिना कारण अनुपस्थिति','无故缺勤','無断欠席','неуважительные пропуски'],
    'sem contar as justificadas':['excluding excused absences','sin contar las justificadas','hors absences justifiées','entschuldigte Fehlzeiten ausgenommen','باستثناء الغياب المبرر','स्वीकृत अनुपस्थिति छोड़कर','不含已请假缺勤','正当な欠席を除く','без учёта уважительных пропусков'],
    'primeira entrada de cada aluno hoje':['each student’s first entry today','primera entrada de cada alumno hoy','première entrée de chaque élève aujourd’hui','erster Eintritt jedes Schülers heute','أول دخول لكل طالب اليوم','आज हर छात्र का पहला प्रवेश','每名学生今日首次进入','各生徒の今日の最初の入室','первый приход каждого ученика сегодня'],
    'entradas e saídas':['entries and exits','entradas y salidas','entrées et sorties','Ein- und Ausgänge','الدخول والخروج','प्रवेश और निकास','进出记录','入退室','приходы и уходы'],
    'quando a câmera mais alerta':['when the camera sends most alerts','cuándo alerta más la cámara','quand la caméra alerte le plus','wann die Kamera am häufigsten warnt','متى تصدر الكاميرا معظم التنبيهات','कैमरा सबसे अधिक अलर्ट कब देता है','摄像头警报最多的时段','カメラの警告が多い時間帯','когда камера чаще всего предупреждает'],
    'acompanhamento da coordenação':['coordination follow-up','seguimiento de coordinación','suivi de la coordination','Nachverfolgung durch die Koordination','متابعة الإدارة','समन्वय टीम की निगरानी','教务跟进','運営側の確認','контроль администрации'],
    'Modo da câmera':['Camera mode','Modo de cámara','Mode de la caméra','Kameramodus','وضع الكاميرا','कैमरा मोड','摄像头模式','カメラモード','Режим камеры'],
    'Ocorrências de segurança':['Security incidents','Incidencias de seguridad','Incidents de sécurité','Sicherheitsvorfälle','الحوادث الأمنية','सुरक्षा घटनाएँ','安保事件','セキュリティ事案','События безопасности'],
    'Aviso ao responsável':['Guardian notification','Aviso al responsable','Avis au responsable','Mitteilung an Erziehungsberechtigte','إشعار ولي الأمر','अभिभावक सूचना','监护人通知','保護者への通知','Уведомление опекуну'],
    'Avisos não entregues':['Undelivered notifications','Avisos no entregados','Avis non remis','Nicht zugestellte Mitteilungen','إشعارات لم تُسلّم','नहीं पहुँची सूचनाएँ','未送达通知','未配信の通知','Недоставленные уведомления'],
    'Mensagens de hoje que o Telegram recusou':['Messages rejected by Telegram today','Mensajes rechazados por Telegram hoy','Messages rejetés par Telegram aujourd’hui','Heute von Telegram abgelehnte Nachrichten','رسائل رفضها تيليغرام اليوم','आज टेलीग्राम द्वारा अस्वीकृत संदेश','今日被 Telegram 拒绝的消息','今日Telegramに拒否されたメッセージ','Сообщения, отклонённые Telegram сегодня'],
    'Retiradas de hoje autorizadas pelos responsáveis no bot':['Today’s pickups authorized by guardians in the bot','Recogidas de hoy autorizadas por responsables en el bot','Retraits du jour autorisés par les responsables via le bot','Heute im Bot von Erziehungsberechtigten autorisierte Abholungen','استلامات اليوم المصرح بها من أولياء الأمور عبر البوت','आज बॉट में अभिभावकों द्वारा अधिकृत पिकअप','今日由监护人在机器人中授权的接走记录','保護者がボットで承認した今日の引き取り','Заборы сегодня, разрешённые опекунами в боте'],
    'retiradas, avisos aos responsáveis e convites pendentes':['pickups, guardian notifications and pending invitations','recogidas, avisos a responsables e invitaciones pendientes','retraits, avis aux responsables et invitations en attente','Abholungen, Elternmitteilungen und ausstehende Einladungen','الاستلامات وإشعارات أولياء الأمور والدعوات المعلقة','पिकअप, अभिभावक सूचनाएँ और लंबित निमंत्रण','接走记录、监护人通知与待处理邀请','引き取り、保護者通知、保留中の招待','заборы, уведомления опекунам и ожидающие приглашения'],
    'Alunos cujo responsável não recebe nenhum aviso':['Students whose guardian receives no notifications','Alumnos cuyo responsable no recibe avisos','Élèves dont le responsable ne reçoit aucun avis','Schüler, deren Erziehungsberechtigte keine Mitteilungen erhalten','طلاب لا يتلقى أولياؤهم أي إشعارات','जिन छात्रों के अभिभावक को सूचनाएँ नहीं मिलतीं','监护人未收到通知的学生','保護者に通知が届いていない生徒','Ученики, чьи опекуны не получают уведомлений'],
    'Hoje: passagens da câmera e alertas de acesso, em tempo real · clique numa turma acima para filtrar':['Today: camera passages and access alerts in real time · select a class above to filter','Hoy: pasos ante la cámara y alertas de acceso en tiempo real · selecciona una clase para filtrar','Aujourd’hui : passages caméra et alertes d’accès en direct · choisissez une classe pour filtrer','Heute: Kamerapassagen und Zugangswarnungen in Echtzeit · Klasse zum Filtern wählen','اليوم: مرور أمام الكاميرا وتنبيهات الدخول مباشرة · اختر صفًا للتصفية','आज: कैमरा गतिविधि और प्रवेश अलर्ट वास्तविक समय में · फ़िल्टर के लिए कक्षा चुनें','今日：摄像头通行与实时门禁警报 · 选择班级筛选','今日：カメラ通過とアクセス警告をリアルタイム表示・クラスで絞り込み','Сегодня: проходы перед камерой и предупреждения о доступе · выберите класс для фильтра'],
    'rosto obstruído · decisões da portaria':['obstructed face · security decisions','rostro obstruido · decisiones de portería','visage masqué · décisions de l’accueil','verdecktes Gesicht · Sicherheitsentscheidungen','وجه محجوب · قرارات الأمن','ढका चेहरा · सुरक्षा निर्णय','面部遮挡 · 安保处理','顔の遮蔽・警備の判断','закрытое лицо · решения охраны'],
    'Assistente RealGuard':['RealGuard Assistant','Asistente RealGuard','Assistant RealGuard','RealGuard-Assistent','مساعد RealGuard','RealGuard सहायक','RealGuard 助手','RealGuardアシスタント','Ассистент RealGuard'],
    'Saídas antecipadas recorrentes':['Repeated early departures','Salidas anticipadas recurrentes','Départs anticipés répétés','Wiederholtes frühes Gehen','مغادرات مبكرة متكررة','बार-बार जल्दी जाना','重复提前离开','繰り返す早退','Повторяющиеся ранние уходы'],
    'Saídas com outra pessoa':['Departures with another person','Salidas con otra persona','Départs avec une autre personne','Abgänge mit einer anderen Person','خروج مع شخص آخر','दूसरे व्यक्ति के साथ प्रस्थान','与其他人离开','別の人との退出','Уходы с другим человеком'],
    'Primeira entrada de cada aluno':['Each student’s first entry','Primera entrada de cada alumno','Première entrée de chaque élève','Erster Eintritt jedes Schülers','أول دخول لكل طالب','हर छात्र का पहला प्रवेश','每名学生首次进入','各生徒の最初の入室','Первый приход каждого ученика'],
    'primeira entrada de cada aluno':['each student’s first entry','primera entrada de cada alumno','première entrée de chaque élève','erster Eintritt jedes Schülers','أول دخول لكل طالب','हर छात्र का पहला प्रवेश','每名学生首次进入','各生徒の最初の入室','первый приход каждого ученика'],
    'última passagem':['last passage','último paso','dernier passage','letzter Durchgang','آخر مرور','अंतिम बार देखा गया','最后通行','最後の通過','последний проход'],
    'Última passagem':['Last passage','Último paso','Dernier passage','Letzter Durchgang','آخر مرور','अंतिम बार देखा गया','最后通行','最後の通過','Последний проход'],
    'Presença por turma':['Attendance by class','Asistencia por clase','Présence par classe','Anwesenheit nach Klasse','الحضور حسب الصف','कक्षा के अनुसार उपस्थिति','各班出勤情况','クラス別の出席','Посещаемость по классам'],
    'Atividades recentes':['Recent activity','Actividad reciente','Activités récentes','Letzte Aktivitäten','الأنشطة الأخيرة','हाल की गतिविधि','最近活动','最近のアクティビティ','Недавние действия'],
    'Logs técnicos e auditoria':['System and audit log','Registro técnico y auditoría','Journal technique et audit','Technische Protokolle und Prüfung','سجل النظام والتدقيق','सिस्टम और ऑडिट लॉग','系统与审计日志','システム・監査ログ','Системный журнал и аудит'],
    'Ver relatório completo':['View full report','Ver informe completo','Voir le rapport complet','Vollständigen Bericht ansehen','عرض التقرير الكامل','पूरी रिपोर्ट देखें','查看完整报告','詳細レポートを見る','Полный отчёт'],
    'Chegaram depois do horário-limite':['Arrived after the cutoff time','Llegaron después de la hora límite','Arrivés après l’heure limite','Nach Fristende angekommen','وصلوا بعد الموعد النهائي','निर्धारित समय के बाद आए','超过截止时间到达','締切時刻後に到着','Пришли после установленного времени'],
    'dos esperados estão na escola':['of expected students are at school','de los esperados están en la escuela','des élèves attendus sont à l’école','der erwarteten Schüler sind in der Schule','من المتوقعين في المدرسة','अपेक्षित छात्रों में से स्कूल में हैं','预期学生已到校','登校予定の生徒が在校中','ожидаемых учеников находятся в школе'],
    'já saíram':['have left','ya salieron','sont déjà partis','sind bereits gegangen','غادروا','जा चुके हैं','已离开','退出済み','уже ушли'],
    'alertas a decidir':['alerts to review','alertas por decidir','alertes à traiter','offene Warnungen','تنبيهات للمراجعة','समीक्षा के लिए अलर्ट','待处理警报','確認待ちの警告','предупреждений для решения'],
    'Validações de identificação facial que exigem decisão da portaria':['Face identification checks requiring a security decision','Validaciones faciales que requieren una decisión de portería','Vérifications faciales nécessitant une décision de l’accueil','Gesichtsprüfungen, die eine Entscheidung erfordern','عمليات تحقق بالوجه تتطلب قرار الأمن','चेहरे की पहचान जिन पर सुरक्षा निर्णय चाहिए','需要安保人员处理的人脸识别验证','警備員による判断が必要な顔認証','Проверки распознавания лиц, требующие решения охраны'],
    'Foto do momento: confirme ou descarte. A equipe também decide pelo Telegram.':['Snapshot: confirm or dismiss. The team can also decide through Telegram.','Foto del momento: confirma o descarta. El equipo también puede decidir por Telegram.','Instantané : confirmer ou écarter. L’équipe peut aussi décider via Telegram.','Momentaufnahme: bestätigen oder verwerfen. Das Team kann auch über Telegram entscheiden.','لقطة فورية: أكّد أو تجاهل. يمكن للفريق اتخاذ القرار عبر تيليغرام أيضًا.','वर्तमान तस्वीर: पुष्टि या खारिज करें। टीम टेलीग्राम से भी निर्णय ले सकती है।','实时截图：确认或忽略。团队也可通过 Telegram 处理。','その場の写真：確認または破棄します。Telegramでも判断できます。','Снимок момента: подтвердите или отклоните. Команда также может решить через Telegram.'],
    'o que já foi decidido, por quem, por onde e em quanto tempo':['what was decided, by whom, where and how quickly','qué se decidió, quién lo hizo, dónde y en cuánto tiempo','décisions prises, par qui, où et en combien de temps','was wann, von wem und wo entschieden wurde','ما تم تقريره ومن اتخذ القرار ومتى وأين','क्या निर्णय हुआ, किसने, कहाँ और कितने समय में','已处理的事项、处理人、地点及耗时','判断内容・担当者・場所・所要時間','что было решено, кем, где и за какое время'],
    'Cadastro com reconhecimento facial':['Registration with face recognition','Registro con reconocimiento facial','Inscription avec reconnaissance faciale','Registrierung mit Gesichtserkennung','تسجيل باستخدام التعرف على الوجه','चेहरा पहचान के साथ पंजीकरण','人脸识别登记','顔認証による登録','Регистрация с распознаванием лиц'],
    'Demonstração: os números replicam a câmera real.':['Demo: the numbers mirror the real camera.','Demostración: las cifras reflejan la cámara real.','Démo : les chiffres reflètent la caméra réelle.','Demo: Die Zahlen spiegeln die echte Kamera wider.','عرض تجريبي: الأرقام تعكس الكاميرا الحقيقية.','डेमो: आँकड़े वास्तविक कैमरे जैसे हैं।','演示：数字与真实摄像头一致。','デモ：数値は実際のカメラを反映します。','Демо: показатели соответствуют реальной камере.'],
    'tira dúvidas sobre como usar o sistema':['answers questions about using the system','responde dudas sobre el uso del sistema','répond aux questions sur le système','beantwortet Fragen zur Systemnutzung','يجيب عن أسئلة استخدام النظام','सिस्टम के उपयोग के बारे में सवालों के जवाब देता है','解答系统使用问题','システムの使い方について回答します','отвечает на вопросы об использовании системы'],
    'atualizado':['updated','actualizado','mis à jour','aktualisiert','تم التحديث','अपडेट किया गया','已更新','更新済み','обновлено'],
    'Recolher menu':['Collapse menu','Contraer menú','Réduire le menu','Menü einklappen','طي القائمة','मेनू समेटें','收起菜单','メニューを折りたたむ','Свернуть меню'],
    'Expandir menu':['Expand menu','Expandir menú','Développer le menu','Menü ausklappen','توسيع القائمة','मेनू खोलें','展开菜单','メニューを展開','Развернуть меню'],
    'ontem':['yesterday','ayer','hier','gestern','أمس','कल','昨天','昨日','вчера'],
    'hoje':['today','hoy','aujourd’hui','heute','اليوم','आज','今天','今日','сегодня'],
    'Ver alunos presentes':['View present students','Ver alumnos presentes','Voir les élèves présents','Anwesende Schüler ansehen','عرض الطلاب الحاضرين','उपस्थित छात्र देखें','查看在场学生','出席中の生徒を見る','Посмотреть присутствующих'],
    'Ver ausentes de hoje':['View today’s absences','Ver ausentes de hoy','Voir les absents du jour','Heutige Fehlzeiten ansehen','عرض غياب اليوم','आज के अनुपस्थित छात्रों को देखें','查看今日缺勤','今日の欠席者を見る','Посмотреть отсутствующих сегодня'],
    'Ver lista de atrasos':['View late arrivals','Ver retrasos','Voir les retards','Verspätungen ansehen','عرض المتأخرين','देरी से आए छात्रों को देखें','查看迟到名单','遅刻者を見る','Посмотреть опоздания'],
    'Nenhum aluno chegou ainda hoje':['No students have arrived today','Ningún alumno ha llegado hoy','Aucun élève n’est encore arrivé aujourd’hui','Heute ist noch kein Schüler angekommen','لم يصل أي طالب اليوم','आज अभी कोई छात्र नहीं आया','今天尚无学生到达','今日はまだ生徒が来ていません','Сегодня ученики ещё не приходили'],
    'Presenças do dia':['Today’s attendance','Asistencias del día','Présences du jour','Heutige Anwesenheit','حضور اليوم','आज की उपस्थिति','今日出勤记录','今日の出席','Посещения за день'],
    'Pessoas em cena':['People in view','Personas en escena','Personnes visibles','Personen im Bild','أشخاص في المشهد','दृश्य में लोग','画面人数','画面内の人数','Людей в кадре'],
    'pessoas em cena':['people in view','personas en escena','personnes visibles','Personen im Bild','أشخاص في المشهد','दृश्य में लोग','画面人数','画面内の人数','людей в кадре'],
    'pessoa em cena':['person in view','persona en escena','personne visible','Person im Bild','شخص في المشهد','दृश्य में व्यक्ति','画面中的人员','画面内の人物','человек в кадре'],
    'no recinto':['on site','en el recinto','sur place','vor Ort','في الموقع','परिसर में','在场','構内','на территории'],
    'Presente na sala':['Present in room','Presente en el aula','Présent dans la salle','Im Raum anwesend','حاضر في القاعة','कक्षा में उपस्थित','在教室','教室に在室','Присутствует в классе'],
    'Sem turma':['No class','Sin clase','Sans classe','Keine Klasse','بلا صف','कोई कक्षा नहीं','无班级','クラスなし','Без класса'],
    'Identificando...':['Identifying...','Identificando...','Identification...','Identifizierung...','جارٍ التعرف...','पहचान जारी...','识别中...','識別中...','Идентификация...'],
    'Não cadastrado':['Unregistered','No registrado','Non inscrit','Nicht registriert','غير مسجل','अपंजीकृत','未登记','未登録','Не зарегистрирован'],
    'Reconhecido':['Recognized','Reconocido','Reconnu','Erkannt','تم التعرف عليه','पहचाना गया','已识别','認識済み','Опознан'],
    'Confirmado':['Confirmed','Confirmado','Confirmé','Bestätigt','مؤكد','पुष्टि हुई','已确认','確認済み','Подтверждено'],
    'Turma selecionada':['Selected class','Clase seleccionada','Classe sélectionnée','Ausgewählte Klasse','الصف المحدد','चयनित कक्षा','已选班级','選択中のクラス','Выбранный класс'],
    'Clique para usar':['Click to select','Haz clic para usar','Cliquez pour choisir','Zum Auswählen klicken','انقر للاختيار','चुनने के लिए क्लिक करें','点击选择','クリックして選択','Нажмите для выбора'],
    'clique para usar':['click to select','haz clic para usar','cliquez pour choisir','zum Auswählen klicken','انقر للاختيار','चुनने के लिए क्लिक करें','点击选择','クリックして選択','нажмите для выбора'],
    'turma desta sala':['class in this room','clase de esta aula','classe de cette salle','Klasse dieses Raums','صف هذه القاعة','इस कक्ष की कक्षा','本教室班级','この教室のクラス','класс этой аудитории'],
    'Observação atual':['Current view','Observación actual','Vue actuelle','Aktuelle Ansicht','العرض الحالي','वर्तमान दृश्य','当前画面','現在の表示','Текущий вид'],
    'observação atual':['current view','observación actual','vue actuelle','aktuelle Ansicht','العرض الحالي','वर्तमान दृश्य','当前画面','現在の表示','текущий вид'],
    'Categorias podem se sobrepor':['Categories may overlap','Las categorías pueden solaparse','Les catégories peuvent se chevaucher','Kategorien können sich überschneiden','قد تتداخل الفئات','श्रेणियाँ एक दूसरे से मिल सकती हैं','分类可能重叠','分類は重複する場合があります','Категории могут пересекаться'],
    'Registrado em':['Recorded at','Registrado a las','Enregistré à','Erfasst um','سُجل في','दर्ज समय','记录时间','記録時刻','Записано в'],
    'Nenhuma presença completou 30 segundos de identificação contínua nesta sessão.':['No attendance has reached 30 seconds of continuous identification in this session.','Ninguna presencia ha completado 30 segundos de identificación continua en esta sesión.','Aucune présence n’a atteint 30 secondes d’identification continue pendant cette session.','In dieser Sitzung wurden noch keine 30 Sekunden kontinuierliche Erkennung erreicht.','لم يبلغ أي حضور 30 ثانية من التعرف المستمر في هذه الجلسة.','इस सत्र में अभी किसी की लगातार 30 सेकंड पहचान नहीं हुई है।','本次会话尚无人完成连续 30 秒识别。','このセッションで連続30秒認識された出席者はいません。','В этой сессии никто ещё не прошёл 30 секунд непрерывного распознавания.'],
    'Da turma':['From this class','De la clase','De cette classe','Aus dieser Klasse','من هذه الصف','इस कक्षा से','本班学生','このクラス','Из этого класса'],
    'De outra turma':['From another class','De otra clase','D’une autre classe','Aus einer anderen Klasse','من صف آخر','दूसरी कक्षा से','其他班级','別のクラス','Из другого класса'],
    'Composição da sala':['Room composition','Composición del aula','Composition de la salle','Zusammensetzung des Raums','تكوين القاعة','कक्षा की संरचना','教室构成','教室の内訳','Состав класса'],
    'Turma da sala':['Room class','Clase del aula','Classe de la salle','Klasse des Raums','صف القاعة','कक्ष की कक्षा','教室班级','教室のクラス','Класс аудитории'],
    'Turma da câmera':['Camera class','Clase de la cámara','Classe de la caméra','Kameraklasse','صف الكاميرا','कैमरे की कक्षा','摄像头班级','カメラのクラス','Класс камеры'],
    'Presença registrada na sala':['Attendance recorded in room','Asistencia registrada en el aula','Présence enregistrée en salle','Anwesenheit im Raum erfasst','تم تسجيل الحضور في القاعة','कक्षा में उपस्थिति दर्ज','教室出勤记录','教室の出席記録','Посещаемость в классе'],
    'Presença registrada':['Attendance recorded','Asistencia registrada','Présence enregistrée','Anwesenheit erfasst','تم تسجيل الحضور','उपस्थिति दर्ज','已记录出勤','出席記録済み','Посещение отмечено'],
    'Tempo em cena':['Time in view','Tiempo en escena','Temps visible','Zeit im Bild','الوقت في المشهد','दृश्य में समय','画面停留时间','画面内の時間','Время в кадре'],
    'Registrado às':['Recorded at','Registrado a las','Enregistré à','Erfasst um','سُجل في','दर्ज समय','记录时间','記録時刻','Записано в'],
    'Situação':['Status','Estado','Situation','Status','الحالة','स्थिति','状态','状況','Статус'],
    'Buscar pessoa':['Search person','Buscar persona','Rechercher une personne','Person suchen','ابحث عن شخص','व्यक्ति खोजें','搜索人员','人物を検索','Найти человека'],
    'Pesquisar pessoa':['Search person','Buscar persona','Rechercher une personne','Person suchen','ابحث عن شخص','व्यक्ति खोजें','搜索人员','人物を検索','Найти человека'],
    'Câmera Principal':['Main camera','Cámara principal','Caméra principale','Hauptkamera','الكاميرا الرئيسية','मुख्य कैमरा','主摄像头','メインカメラ','Основная камера'],
    'Adicionar / testar câmera':['Add / test camera','Añadir / probar cámara','Ajouter / tester une caméra','Kamera hinzufügen / testen','إضافة / اختبار كاميرا','कैमरा जोड़ें / जाँचें','添加 / 测试摄像头','カメラを追加 / テスト','Добавить / проверить камеру'],
    'Definir turma da câmera':['Set camera class','Asignar clase a la cámara','Définir la classe de la caméra','Kameraklasse festlegen','حدد صف الكاميرا','कैमरे की कक्षा तय करें','设置摄像头班级','カメラのクラスを設定','Задать класс камеры'],
    'Agora na câmera':['Now on camera','Ahora en cámara','Actuellement à la caméra','Jetzt vor der Kamera','الآن أمام الكاميرا','अभी कैमरे पर','当前画面','現在のカメラ','Сейчас в кадре'],
    'Desempenho':['Performance','Rendimiento','Performances','Leistung','الأداء','प्रदर्शन','性能','性能','Производительность'],
    'FPS do vídeo':['Video FPS','FPS del vídeo','FPS vidéo','Video-FPS','إطارات الفيديو/ث','वीडियो FPS','视频帧率','映像FPS','FPS видео'],
    'FPS do processamento':['Processing FPS','FPS de procesamiento','FPS du traitement','Verarbeitungs-FPS','إطارات المعالجة/ث','प्रसंस्करण FPS','处理帧率','処理FPS','FPS обработки'],
    'FPS da câmera':['Camera FPS','FPS de la cámara','FPS caméra','Kamera-FPS','إطارات الكاميرا/ث','कैमरा FPS','摄像头帧率','カメラFPS','FPS камеры'],
    'Resolução':['Resolution','Resolución','Résolution','Auflösung','الدقة','रिज़ॉल्यूशन','分辨率','解像度','Разрешение'],
    'Detalhes':['Details','Detalles','Détails','Details','التفاصيل','विवरण','详情','詳細','Подробности'],
    'Cadastros':['Registrations','Registros','Inscriptions','Registrierungen','التسجيلات','पंजीकरण','登记','登録','Регистрации'],
    'Cadastrados':['Registered','Registrados','Inscrits','Registriert','مسجلون','पंजीकृत','已登记','登録済み','Зарегистрированы'],
    'Com foto':['With photo','Con foto','Avec photo','Mit Foto','مع صورة','फ़ोटो सहित','有照片','写真あり','С фото'],
    'Vistos hoje':['Seen today','Vistos hoy','Vus aujourd’hui','Heute gesehen','شوهدوا اليوم','आज देखे गए','今日识别','今日検出','Замечены сегодня'],
    'Novo aluno':['New student','Nuevo alumno','Nouvel élève','Neuer Schüler','طالب جديد','नया छात्र','新学生','新規生徒','Новый ученик'],
    'Buscar aluno, RM ou responsável':['Search student, ID or guardian','Buscar alumno, ID o responsable','Rechercher un élève, ID ou responsable','Schüler, ID oder Erziehungsberechtigte suchen','ابحث عن طالب أو رقم أو ولي أمر','छात्र, आईडी या अभिभावक खोजें','搜索学生、编号或监护人','生徒・ID・保護者を検索','Поиск ученика, номера или опекуна'],
    'Todas as turmas':['All classes','Todas las clases','Toutes les classes','Alle Klassen','كل الصفوف','सभी कक्षाएँ','所有班级','全クラス','Все классы'],
    'Turmas':['Classes','Clases','Classes','Klassen','الصفوف','कक्षाएँ','班级','クラス','Классы'],
    'Turnos':['Shifts','Turnos','Périodes','Schichten','الفترات','शिफ्ट','时段','時間帯','Смены'],
    'Calendário':['Calendar','Calendario','Calendrier','Kalender','التقويم','कैलेंडर','日历','カレンダー','Календарь'],
    'Manhã':['Morning','Mañana','Matin','Morgen','صباحًا','सुबह','上午','午前','Утро'],
    'Tarde':['Afternoon','Tarde','Après-midi','Nachmittag','بعد الظهر','दोपहर','下午','午後','День'],
    'Noite':['Evening','Noche','Soir','Abend','مساءً','शाम','晚上','夜間','Вечер'],
    'Novo turno':['New shift','Nuevo turno','Nouvelle période','Neue Schicht','فترة جديدة','नई शिफ्ट','新时段','新しい時間帯','Новая смена'],
    'Nova turma':['New class','Nueva clase','Nouvelle classe','Neue Klasse','صف جديد','नई कक्षा','新班级','新しいクラス','Новый класс'],
    'Editar':['Edit','Editar','Modifier','Bearbeiten','تعديل','संपादित करें','编辑','編集','Изменить'],
    'Salvar':['Save','Guardar','Enregistrer','Speichern','حفظ','सहेजें','保存','保存','Сохранить'],
    'Cancelar':['Cancel','Cancelar','Annuler','Abbrechen','إلغاء','रद्द करें','取消','キャンセル','Отмена'],
    'Excluir':['Delete','Eliminar','Supprimer','Löschen','حذف','हटाएँ','删除','削除','Удалить'],
    'Fechar':['Close','Cerrar','Fermer','Schließen','إغلاق','बंद करें','关闭','閉じる','Закрыть'],
    'Pesquisar':['Search','Buscar','Rechercher','Suchen','بحث','खोजें','搜索','検索','Поиск'],
    'Todos':['All','Todos','Tous','Alle','الكل','सभी','全部','すべて','Все'],
    'Atualizar':['Refresh','Actualizar','Actualiser','Aktualisieren','تحديث','अपडेट करें','刷新','更新','Обновить'],
    'Monitores':['Monitors','Monitores','Moniteurs','Monitore','الشاشات','मॉनिटर','监控屏','モニター','Мониторы'],
    'Gerenciador de monitores':['Monitor manager','Gestor de monitores','Gestionnaire des moniteurs','Monitorverwaltung','إدارة الشاشات','मॉनिटर प्रबंधक','监控屏管理','モニター管理','Управление мониторами'],
    'Abrir o dashboard':['Open dashboard','Abrir el panel','Ouvrir le tableau de bord','Dashboard öffnen','فتح لوحة المعلومات','डैशबोर्ड खोलें','打开仪表盘','ダッシュボードを開く','Открыть панель'],
    'Tela cheia':['Full screen','Pantalla completa','Plein écran','Vollbild','ملء الشاشة','पूर्ण स्क्रीन','全屏','全画面','Во весь экран'],
  };
  Object.assign(rows, {
    'Pré-configuração':['Pre-configuration','Configuración inicial','Préconfiguration','Vorkonfiguration','الإعداد الأولي','प्रारंभिक सेटअप','预配置','初期設定','Предварительная настройка'],
    'Defina a apresentação, a câmera e o acesso à internet antes de iniciar o RealGuard.':['Choose the presentation mode, camera and internet access before starting RealGuard.','Defina el modo de presentación, la cámara y el acceso a internet antes de iniciar RealGuard.','Définissez le mode de présentation, la caméra et l’accès à Internet avant de démarrer RealGuard.','Legen Sie Präsentationsmodus, Kamera und Internetzugang fest, bevor Sie RealGuard starten.','حدد وضع العرض والكاميرا والوصول إلى الإنترنت قبل تشغيل RealGuard.','RealGuard शुरू करने से पहले प्रस्तुति मोड, कैमरा और इंटरनेट पहुँच चुनें।','启动 RealGuard 前，请设置演示模式、摄像头和互联网连接。','RealGuard の起動前に、表示モード、カメラ、インターネット接続を設定してください。','Перед запуском RealGuard выберите режим, камеру и подключение к интернету.'],
    'Modo de execução':['Run mode','Modo de ejecución','Mode de fonctionnement','Betriebsmodus','وضع التشغيل','संचालन मोड','运行模式','実行モード','Режим работы'],
    'RealGuard completo':['Full RealGuard','RealGuard completo','RealGuard complet','Vollständiger RealGuard','RealGuard الكامل','पूरा RealGuard','完整 RealGuard','完全版 RealGuard','Полный RealGuard'],
    'Arena · sala de aula':['Arena · classroom','Arena · aula','Arena · salle de classe','Arena · Klassenzimmer','الساحة · الفصل الدراسي','एरिना · कक्षा','竞技场 · 教室','アリーナ · 教室','Арена · класс'],
    'As configurações são salvas neste equipamento e aplicadas ao iniciar.':['Settings are saved on this device and applied at startup.','La configuración se guarda en este equipo y se aplica al iniciar.','Les paramètres sont enregistrés sur cet appareil et appliqués au démarrage.','Die Einstellungen werden auf diesem Gerät gespeichert und beim Start angewendet.','تُحفظ الإعدادات على هذا الجهاز وتُطبّق عند بدء التشغيل.','सेटिंग इस डिवाइस पर सहेजी जाती हैं और शुरू होने पर लागू होती हैं।','设置保存在本设备上，并在启动时生效。','設定はこの端末に保存され、起動時に適用されます。','Настройки сохраняются на устройстве и применяются при запуске.'],
    'Vídeo':['Video','Vídeo','Vidéo','Video','الفيديو','वीडियो','视频','映像','Видео'],
    'Fonte de vídeo':['Video source','Fuente de vídeo','Source vidéo','Videoquelle','مصدر الفيديو','वीडियो स्रोत','视频来源','映像ソース','Источник видео'],
    'Webcam do computador':['Computer webcam','Cámara web del equipo','Webcam de l’ordinateur','Webcam des Computers','كاميرا الكمبيوتر','कंप्यूटर वेबकैम','电脑网络摄像头','パソコンのウェブカメラ','Веб-камера компьютера'],
    'Câmera IP (RTSP)':['IP camera (RTSP)','Cámara IP (RTSP)','Caméra IP (RTSP)','IP-Kamera (RTSP)','كاميرا IP ‏(RTSP)','IP कैमरा (RTSP)','IP 摄像头 (RTSP)','IP カメラ (RTSP)','IP-камера (RTSP)'],
    'Automática · IP ou webcam':['Automatic · IP or webcam','Automática · IP o webcam','Automatique · IP ou webcam','Automatisch · IP oder Webcam','تلقائي · كاميرا IP أو ويب','स्वचालित · IP या वेबकैम','自动 · IP 或网络摄像头','自動 · IP またはウェブカメラ','Автоматически · IP или веб-камера'],
    'Índice da webcam':['Webcam index','Índice de webcam','Indice de la webcam','Webcam-Index','رقم كاميرا الويب','वेबकैम सूचकांक','网络摄像头编号','ウェブカメラ番号','Номер веб-камеры'],
    'IP da câmera':['Camera IP','IP de la cámara','IP de la caméra','Kamera-IP','عنوان IP للكاميرا','कैमरा IP','摄像头 IP','カメラ IP','IP-адрес камеры'],
    'Porta RTSP':['RTSP port','Puerto RTSP','Port RTSP','RTSP-Port','منفذ RTSP','RTSP पोर्ट','RTSP 端口','RTSP ポート','Порт RTSP'],
    'Caminho RTSP':['RTSP path','Ruta RTSP','Chemin RTSP','RTSP-Pfad','مسار RTSP','RTSP पथ','RTSP 路径','RTSP パス','Путь RTSP'],
    'Usuário da câmera':['Camera username','Usuario de la cámara','Utilisateur de la caméra','Kamera-Benutzername','اسم مستخدم الكاميرا','कैमरा उपयोगकर्ता','摄像头用户名','カメラのユーザー名','Имя пользователя камеры'],
    'Senha da câmera':['Camera password','Contraseña de la cámara','Mot de passe de la caméra','Kamera-Passwort','كلمة مرور الكاميرا','कैमरा पासवर्ड','摄像头密码','カメラのパスワード','Пароль камеры'],
    'Deixe vazio para manter a atual':['Leave blank to keep the current one','Deje en blanco para conservar la actual','Laissez vide pour conserver la valeur actuelle','Leer lassen, um den aktuellen Wert zu behalten','اتركه فارغًا للاحتفاظ بالحالي','मौजूदा मान रखने के लिए खाली छोड़ें','留空以保留当前值','現在の値を維持する場合は空欄','Оставьте пустым, чтобы сохранить текущее значение'],
    'Testar câmera':['Test camera','Probar cámara','Tester la caméra','Kamera testen','اختبار الكاميرا','कैमरा जाँचें','测试摄像头','カメラをテスト','Проверить камеру'],
    'Internet':['Internet','Internet','Internet','Internet','الإنترنت','इंटरनेट','互联网','インターネット','Интернет'],
    'Conexão':['Connection','Conexión','Connexion','Verbindung','الاتصال','कनेक्शन','连接','接続','Подключение'],
    'Direta · sem proxy':['Direct · no proxy','Directa · sin proxy','Directe · sans proxy','Direkt · ohne Proxy','مباشر · دون وسيط','सीधा · बिना प्रॉक्सी','直连 · 不使用代理','直接接続 · プロキシなし','Напрямую · без прокси'],
    'Proxy do notebook mestre':['Host laptop proxy','Proxy del portátil principal','Proxy de l’ordinateur principal','Proxy des Hauptlaptops','وكيل الحاسوب الرئيسي','मुख्य लैपटॉप प्रॉक्सी','主笔记本代理','メインノート PC のプロキシ','Прокси основного ноутбука'],
    'IP do proxy':['Proxy IP','IP del proxy','IP du proxy','Proxy-IP','عنوان IP للوكيل','प्रॉक्सी IP','代理 IP','プロキシ IP','IP прокси'],
    'Porta do proxy':['Proxy port','Puerto del proxy','Port du proxy','Proxy-Port','منفذ الوكيل','प्रॉक्सी पोर्ट','代理端口','プロキシポート','Порт прокси'],
    'Usuário do proxy':['Proxy username','Usuario del proxy','Utilisateur du proxy','Proxy-Benutzername','اسم مستخدم الوكيل','प्रॉक्सी उपयोगकर्ता','代理用户名','プロキシのユーザー名','Пользователь прокси'],
    'Senha do proxy':['Proxy password','Contraseña del proxy','Mot de passe du proxy','Proxy-Passwort','كلمة مرور الوكيل','प्रॉक्सी पासवर्ड','代理密码','プロキシのパスワード','Пароль прокси'],
    'Testar internet':['Test internet','Probar internet','Tester Internet','Internet testen','اختبار الإنترنت','इंटरनेट जाँचें','测试互联网','インターネットをテスト','Проверить интернет'],
    'Primeiro administrador':['First administrator','Primer administrador','Premier administrateur','Erster Administrator','المسؤول الأول','पहला प्रशासक','首位管理员','最初の管理者','Первый администратор'],
    'Crie a conta que terá acesso à administração.':['Create the account that will manage RealGuard.','Cree la cuenta que administrará RealGuard.','Créez le compte qui administrera RealGuard.','Erstellen Sie das Konto zur Verwaltung von RealGuard.','أنشئ الحساب الذي سيدير RealGuard.','RealGuard प्रबंधित करने वाला खाता बनाएँ।','创建管理 RealGuard 的账户。','RealGuard を管理するアカウントを作成してください。','Создайте учётную запись для управления RealGuard.'],
    'Nome':['Name','Nombre','Nom','Name','الاسم','नाम','姓名','名前','Имя'],
    'Salvar e iniciar':['Save and start','Guardar e iniciar','Enregistrer et démarrer','Speichern und starten','حفظ وتشغيل','सहेजें और शुरू करें','保存并启动','保存して起動','Сохранить и запустить'],
    'Salvar e reiniciar':['Save and restart','Guardar y reiniciar','Enregistrer et redémarrer','Speichern und neu starten','حفظ وإعادة التشغيل','सहेजें और पुनः शुरू करें','保存并重启','保存して再起動','Сохранить и перезапустить'],
    'RealGuard · As senhas permanecem neste equipamento e não são exibidas novamente.':['RealGuard · Passwords stay on this device and are not shown again.','RealGuard · Las contraseñas permanecen en este equipo y no vuelven a mostrarse.','RealGuard · Les mots de passe restent sur cet appareil et ne sont plus affichés.','RealGuard · Passwörter bleiben auf diesem Gerät und werden nicht erneut angezeigt.','RealGuard · تبقى كلمات المرور على هذا الجهاز ولا تُعرض مرة أخرى.','RealGuard · पासवर्ड इसी डिवाइस पर रहते हैं और फिर नहीं दिखाए जाते।','RealGuard · 密码保存在本设备上，不会再次显示。','RealGuard · パスワードはこの端末に保存され、再表示されません。','RealGuard · Пароли остаются на этом устройстве и больше не показываются.'],
  });
  const maps = Object.fromEntries(codes.slice(1).map((code, i) =>
    [code, Object.fromEntries(Object.entries(rows).map(([source, values]) => [source, values[i]]))]));
  const sources = Object.keys(rows).sort((a,b) => b.length - a.length);
  const sourceText = new WeakMap(), renderedText = new WeakMap();
  const sourceAttrs = new WeakMap(), renderedAttrs = new WeakMap();
  const attrs = ['title','aria-label','placeholder'];
  let locale = 'pt-BR', selector, observer, scheduled = false, userChanged = false, fullRender = false;
  const pending = new Set();
  const originalTitle = document.title;
  function translate(source) {
    if (locale === 'pt-BR' || !source) return source;
    const map = maps[locale];
    let text = source;
    for (const key of sources) if (text.includes(key)) text = text.replaceAll(key, map[key]);
    return text;
  }
  function excluded(node) {
    const element = node.nodeType === 1 ? node : node.parentElement;
    return !element || !!element.closest('.rg-locale,script,style,code,pre,[contenteditable],[data-no-i18n]');
  }
  function translateNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      if (excluded(node)) return;
      const previous = renderedText.get(node);
      if (!sourceText.has(node) || node.nodeValue !== previous) sourceText.set(node, node.nodeValue);
      const next = translate(sourceText.get(node));
      renderedText.set(node, next);
      if (node.nodeValue !== next) node.nodeValue = next;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || excluded(node)) return;
    const original = sourceAttrs.get(node) || {}, rendered = renderedAttrs.get(node) || {};
    for (const attr of attrs) {
      if (!node.hasAttribute(attr)) continue;
      const current = node.getAttribute(attr);
      if (!(attr in original) || current !== rendered[attr]) original[attr] = current;
      const next = translate(original[attr]);
      rendered[attr] = next;
      if (current !== next) node.setAttribute(attr, next);
    }
    sourceAttrs.set(node, original); renderedAttrs.set(node, rendered);
    for (const child of node.childNodes) translateNode(child);
  }
  function render() {
    scheduled = false;
    document.documentElement.lang = locale;
    if (fullRender) translateNode(document.body);
    else for (const node of pending) if (node.isConnected) translateNode(node);
    pending.clear(); fullRender = false;
    if (selector) {
      const index = codes.indexOf(locale);
      const flag = selector.querySelector('.rg-locale-current-flag');
      const name = selector.querySelector('.rg-locale-current-name');
      const summary = selector.querySelector('summary');
      if (flag.src !== flagSources[index]) flag.src = flagSources[index];
      if (name.textContent !== names[index]) name.textContent = names[index];
      const label = 'Idioma / Language / Idioma: ' + names[index];
      if (summary.getAttribute('aria-label') !== label) summary.setAttribute('aria-label', label);
      selector.querySelectorAll('[data-locale]').forEach(option => {
        const selected = option.dataset.locale === locale ? 'true' : 'false';
        if (option.getAttribute('aria-pressed') !== selected) option.setAttribute('aria-pressed', selected);
      });
    }
    document.title = translate(originalTitle);
  }
  function schedule(node = null, full = false) {
    if (node) pending.add(node);
    if (full) fullRender = true;
    if (!scheduled) { scheduled = true; requestAnimationFrame(render); }
  }
  function setLocale(value, persist = true) {
    if (!codes.includes(value)) return;
    locale = value;
    if (persist) { try { localStorage.setItem('rg.ui.locale', value); } catch (_) {} }
    schedule(null, true);
    document.dispatchEvent(new CustomEvent('realguard:localechange', {detail:{locale:value}}));
  }
  function makeSelector() {
    selector = document.createElement('details');
    selector.className = 'rg-locale';
    const summary = document.createElement('summary');
    summary.setAttribute('aria-label', 'Idioma / Language / Idioma');
    const currentFlag = document.createElement('img');
    currentFlag.className = 'rg-locale-current-flag';
    currentFlag.src = flagSources[0];
    currentFlag.alt = '';
    const currentName = document.createElement('span');
    currentName.className = 'rg-locale-current-name';
    currentName.textContent = names[0];
    const arrow = document.createElement('span');
    arrow.className = 'rg-locale-arrow';
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '⌄';
    summary.append(currentFlag, currentName, arrow);
    const menu = document.createElement('div');
    menu.className = 'rg-locale-menu';
    codes.forEach((code, i) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.dataset.locale = code;
      option.setAttribute('aria-pressed', code === locale ? 'true' : 'false');
      const flag = document.createElement('img');
      flag.src = flagSources[i];
      flag.alt = '';
      const name = document.createElement('span');
      name.textContent = names[i];
      option.append(flag, name);
      option.addEventListener('click', () => {
        userChanged = true;
        selector.open = false;
        setLocale(code);
        summary.focus();
      });
      menu.append(option);
    });
    selector.append(summary, menu);
    selector.addEventListener('keydown', event => {
      if (event.key === 'Escape' && selector.open) {
        selector.open = false;
        summary.focus();
      } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        selector.open = true;
        const options = Array.from(menu.querySelectorAll('button'));
        const current = options.indexOf(document.activeElement);
        const next = current < 0 ? codes.indexOf(locale) : (current + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
        options[next].focus();
      }
    });
    document.addEventListener('pointerdown', event => {
      if (selector.open && !selector.contains(event.target)) selector.open = false;
    });
    if (document.querySelector('#fLogin')) { selector.classList.add('rg-locale-login'); document.body.append(selector); }
    else if (document.querySelector('.topbar .right')) document.querySelector('.topbar .right').prepend(selector);
    else if (document.querySelector('.bar')) document.querySelector('.bar').append(selector);
    else document.body.append(selector);
  }
  const style = document.createElement('style');
  style.textContent = '.rg-locale{position:relative;display:inline-flex;align-items:center;z-index:1000;flex:none;color:#eef2f8;font:600 12px Archivo,system-ui,sans-serif}.rg-locale summary{display:flex;align-items:center;gap:6px;box-sizing:border-box;max-width:170px;min-height:36px;padding:6px 9px;border-radius:10px;border:1px solid rgba(160,190,240,.22);background:#122038;cursor:pointer;list-style:none}.rg-locale summary::-webkit-details-marker{display:none}.rg-locale summary:hover,.rg-locale[open] summary{border-color:#3987e5}.rg-locale summary:focus-visible,.rg-locale-menu button:focus-visible{outline:2px solid #3987e5;outline-offset:2px}.rg-locale img{display:block;width:18px;height:12px;flex:none;object-fit:cover;border:1px solid rgba(255,255,255,.2);border-radius:2px}.rg-locale-current-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rg-locale-arrow{margin-left:auto;font-size:17px;line-height:10px}.rg-locale-menu{position:absolute;top:calc(100% + 4px);right:0;min-width:190px;max-height:min(380px,70vh);overflow:auto;padding:4px;border:1px solid rgba(160,190,240,.25);border-radius:10px;background:#122038;box-shadow:0 12px 28px rgba(0,0,0,.35)}.rg-locale-menu button{display:flex;align-items:center;gap:8px;width:100%;padding:8px;border:0;border-radius:6px;background:transparent;color:#eef2f8;text-align:left;font:inherit;cursor:pointer}.rg-locale-menu button:hover,.rg-locale-menu button[aria-pressed="true"]{background:#244266}.rg-locale-login{position:fixed;right:18px;top:16px}.rg-locale-login summary{background:#0d1a30}@media(max-width:520px){.rg-locale summary{max-width:126px;font-size:11px}}';
  style.textContent += '.rg-locale-arrow{width:7px;height:7px;margin-left:auto;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:translateY(-2px) rotate(45deg);font-size:0;line-height:0;flex:none}';
  document.head.append(style);
  makeSelector();
  observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'childList') {
        for (const node of record.addedNodes) schedule(node);
      } else {
        schedule(record.target);
      }
    }
  });
  observer.observe(document.body, {subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:attrs});
  let stored = null, previousDefault = null;
  try { stored = localStorage.getItem('rg.ui.locale'); previousDefault = localStorage.getItem('rg.ui.default'); } catch (_) {}
  function applyDefault(raw) {
      const terminalDefault = codes.includes(raw) ? raw : 'pt-BR';
      const chosen = userChanged ? locale : stored && previousDefault === terminalDefault && codes.includes(stored) ? stored : terminalDefault;
      try { localStorage.setItem('rg.ui.default', terminalDefault); } catch (_) {}
      setLocale(chosen, true);
  }
  const cookie = document.cookie.match(/(?:^|;\s*)rg_ui_default=([^;]+)/);
  if (cookie) applyDefault(decodeURIComponent(cookie[1]));
  else fetch('/api/ui/locale', {credentials:'same-origin',cache:'no-store'})
    .then(r => r.ok ? r.json() : {locale:'pt-BR'})
    .then(data => applyDefault(data.locale))
    .catch(() => setLocale(stored && codes.includes(stored) ? stored : 'pt-BR', false));
  window.RealGuardI18n = {setLocale, getLocale:() => locale, translate};
})();
