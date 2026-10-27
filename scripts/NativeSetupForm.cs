using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

internal sealed class NativeSetupForm : Form
{
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern IntPtr SendMessage(IntPtr handle, uint message, IntPtr size, IntPtr icon);
    Icon smallWindowIcon;

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        if (Icon == null) return;
        SendMessage(Handle, 0x80, IntPtr.Zero,
            smallWindowIcon == null ? Icon.Handle : smallWindowIcon.Handle);
        SendMessage(Handle, 0x80, new IntPtr(1), Icon.Handle);
    }

    readonly string root;
    readonly NativeSetupI18n i18n;
    readonly Dictionary<string, Label> labels = new Dictionary<string, Label>();
    readonly Dictionary<string, Button> testButtons = new Dictionary<string, Button>();
    readonly Label subtitle = new Label();
    readonly ComboBox cameraPath = new ComboBox();
    string statusSource = "";
    bool restartOnSave;
    readonly Color bg = Color.FromArgb(7, 16, 31), surface = Color.FromArgb(20, 38, 63);
    readonly Color ink = Color.FromArgb(241, 245, 251), muted = Color.FromArgb(163, 184, 211);
    readonly FlowLayoutPanel flow = new FlowLayoutPanel();
    readonly Dictionary<string, TextBox> fields = new Dictionary<string, TextBox>();
    readonly Dictionary<string, Control> rows = new Dictionary<string, Control>();
    readonly ComboBox language = new ComboBox(), mode = new ComboBox(), camera = new ComboBox(), proxy = new ComboBox();
    readonly Label status = new Label();
    readonly Button save = new Button();
    readonly string[] localeCodes = { "pt-BR", "en", "es", "fr", "de", "ar", "hi", "zh-CN", "ja", "ru" };
    bool needsAdmin, busy;

    internal NativeSetupForm(string path)
    {
        root = path;
        i18n = new NativeSetupI18n(root);
        Text = "RealGuard";
        ClientSize = new Size(610, 690);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = bg; ForeColor = ink; Font = new Font("Segoe UI", 9.5f);
        MaximizeBox = false; ShowIcon = true; ShowInTaskbar = true;
        try
        {
            string iconPath = Path.Combine(root, "web", "brand", "realguard-config.ico");
            Icon = new Icon(iconPath, 32, 32);
            smallWindowIcon = new Icon(iconPath, 16, 16);
        }
        catch { }

        var header = new Panel { Dock = DockStyle.Top, Height = 77, BackColor = bg };
        var logo = new PictureBox { Location = new Point(22, 16), Size = new Size(38, 40), SizeMode = PictureBoxSizeMode.Zoom };
        try { logo.Image = Image.FromFile(Path.Combine(root, "web", "brand", "realguard.png")); } catch { }
        header.Controls.Add(logo);
        header.Controls.Add(new Label { Text = "REALGUARD", AutoSize = true, Location = new Point(70, 20), ForeColor = ink,
            Font = new Font("Segoe UI", 14, FontStyle.Bold) });
        subtitle.Text = "Controle de acesso"; subtitle.AutoSize = true;
        subtitle.Location = new Point(72, 47); subtitle.ForeColor = muted;
        subtitle.Font = new Font("Segoe UI", 8); header.Controls.Add(subtitle);
        Combo(language, new object[] { "Português (Brasil)", "English", "Español", "Français", "Deutsch", "العربية", "हिन्दी", "简体中文", "日本語", "Русский" });
        language.Location = new Point(360, 24); language.Size = new Size(225, 30);
        
        header.Controls.Add(language);

        var footer = new Panel { Dock = DockStyle.Bottom, Height = 68, BackColor = bg };
        status.Location = new Point(22, 12); status.Size = new Size(393, 49); status.ForeColor = muted;
        
        footer.Controls.Add(status);
        save.Text = "Salvar e iniciar"; save.Location = new Point(432, 15); save.Size = new Size(153, 38);
        save.BackColor = Color.FromArgb(221, 181, 94);
        save.ForeColor = bg; save.Font = new Font("Segoe UI", 9, FontStyle.Bold);
        save.FlatStyle = FlatStyle.Flat; save.FlatAppearance.BorderSize = 0;
        save.Click += delegate { Save(); }; footer.Controls.Add(save);

        flow.Dock = DockStyle.Fill; flow.FlowDirection = FlowDirection.TopDown; flow.WrapContents = false;
        flow.AutoScroll = true; flow.Padding = new Padding(20, 6, 10, 8); flow.BackColor = bg;
        Controls.Add(flow); Controls.Add(header); Controls.Add(footer);

        Combo(mode, new object[] { "RealGuard", "Homologação" }); AddChoice("mode", "Modo de execução", mode);
        Combo(camera, new object[] { "Webcam do computador", "Câmera IP (RTSP)", "Automática · IP ou webcam" });
        AddChoice("camera_mode", "Fonte de vídeo", camera);
        AddText("camera_index", "Índice da webcam", "0");
        AddText("camera_ip", "IP da câmera"); AddText("camera_port", "Porta RTSP", "554");
        Combo(cameraPath, new object[] { "/cam/realmonitor?channel=1&subtype=1",
            "/avstream/channel=1/stream=0.sdp" });
        cameraPath.DropDownStyle = ComboBoxStyle.DropDown;
        AddChoice("camera_path", "Caminho RTSP", cameraPath);
        AddText("camera_user", "Usuário da câmera");
        AddText("camera_password", "Senha da câmera · vazio mantém a atual", "", true);
        AddTest("test_camera", "Testar câmera", "camera");
        Combo(proxy, new object[] { "Direta · sem proxy", "Proxy do notebook mestre" });
        AddChoice("proxy_enabled", "Conexão com a internet", proxy);
        AddText("proxy_host", "IP do proxy"); AddText("proxy_port", "Porta do proxy");
        AddText("proxy_user", "Usuário do proxy"); AddText("proxy_password", "Senha do proxy · vazio mantém a atual", "", true);
        AddTest("test_proxy", "Testar internet", "proxy");
        AddText("admin_user", "Primeiro administrador · usuário"); AddText("admin_name", "Nome");
        AddText("admin_password", "Senha do administrador", "", true);
        camera.SelectedIndexChanged += delegate { Visibility(); };
        proxy.SelectedIndexChanged += delegate { Visibility(); };
        language.SelectedIndexChanged += delegate { ApplyLanguage(); };
        language.SelectedIndex = 0;
        Shown += delegate { LoadSettings(); };
    }

    void Combo(ComboBox box, object[] options)
    {
        box.DropDownStyle = ComboBoxStyle.DropDownList; box.FlatStyle = FlatStyle.Flat;
        box.BackColor = surface; box.ForeColor = ink; box.Font = Font; box.Items.AddRange(options);
    }

    string T(string source) { return i18n.Get(Math.Max(0, language.SelectedIndex), source); }
    void TranslateItems(ComboBox box, string[] sources)
    {
        int selected = Math.Max(0, box.SelectedIndex);
        box.BeginUpdate(); box.Items.Clear();
        foreach (string source in sources) box.Items.Add(T(source));
        box.SelectedIndex = Math.Min(selected, box.Items.Count - 1);
        box.EndUpdate();
    }
    void ApplyLanguage()
    {
        subtitle.Text = T("Controle de acesso");
        foreach (var item in labels) item.Value.Text = T(Convert.ToString(item.Value.Tag));
        labels["camera_password"].Text = T("Senha da câmera") + " · " + T("Deixe vazio para manter a atual");
        labels["proxy_password"].Text = T("Senha do proxy") + " · " + T("Deixe vazio para manter a atual");
        labels["admin_user"].Text = T("Primeiro administrador") + " · " + T("Usuário");
        labels["admin_password"].Text = T("Senha") + " · " + T("Primeiro administrador");
        foreach (var item in testButtons) item.Value.Text = T(Convert.ToString(item.Value.Tag));
        TranslateItems(mode, new[] { "RealGuard", "Homologação" });
        TranslateItems(camera, new[] { "Webcam do computador", "Câmera IP (RTSP)", "Automática · IP ou webcam" });
        TranslateItems(proxy, new[] { "Direta · sem proxy", "Proxy do notebook mestre" });
        save.Text = T(restartOnSave ? "Salvar e reiniciar" : "Salvar e iniciar");
        status.Text = T(statusSource);
        Visibility();
    }
    void AddChoice(string key, string label, ComboBox input) { AddRow(key, label, input); }
    void AddText(string key, string label, string initial = "", bool secret = false)
    {
        var box = new TextBox { Text = initial, UseSystemPasswordChar = secret, BackColor = surface,
            ForeColor = ink, BorderStyle = BorderStyle.FixedSingle, Font = Font };
        fields[key] = box; AddRow(key, label, box);
    }
    void AddRow(string key, string label, Control input)
    {
        var panel = new Panel { Width = 550, Height = 62, Margin = new Padding(0, 0, 0, 2) };
        var caption = new Label { Text = label, Tag = label, AutoSize = true, ForeColor = muted, Location = new Point(2, 0) };
        panel.Controls.Add(caption); labels[key] = caption;
        input.Location = new Point(2, 23); input.Size = new Size(530, 29); panel.Controls.Add(input);
        flow.Controls.Add(panel); rows[key] = panel;
    }
    void AddTest(string key, string label, string target)
    {
        var panel = new Panel { Width = 550, Height = 36, Margin = new Padding(0, 0, 0, 4) };
        var button = new Button { Text = label, Location = new Point(2, 0), Size = new Size(135, 29),
            BackColor = surface, ForeColor = ink, FlatStyle = FlatStyle.Flat };
        button.Tag = label; button.Click += delegate { Test(target); };
        panel.Controls.Add(button); flow.Controls.Add(panel); rows[key] = panel; testButtons[key] = button;
    }
    void Visibility()
    {
        bool webcam = camera.SelectedIndex == 0, useProxy = proxy.SelectedIndex == 1;
        rows["camera_index"].Visible = webcam;
        foreach (string key in new[] { "camera_ip", "camera_port", "camera_path", "camera_user", "camera_password", "test_camera" })
            rows[key].Visible = !webcam;
        foreach (string key in new[] { "proxy_host", "proxy_port", "proxy_user", "proxy_password", "test_proxy" })
            rows[key].Visible = useProxy;
        foreach (string key in new[] { "admin_user", "admin_name", "admin_password" })
            rows[key].Visible = needsAdmin;
    }
    Dictionary<string, string> Values()
    {
        var data = new Dictionary<string, string>();
        data["mode"] = mode.SelectedIndex == 1 ? "arena" : "complete";
        data["locale"] = localeCodes[Math.Max(0, language.SelectedIndex)];
        data["camera_mode"] = new[] { "webcam", "rtsp", "auto" }[Math.Max(0, camera.SelectedIndex)];
        data["proxy_enabled"] = proxy.SelectedIndex == 1 ? "true" : "false";
        foreach (var item in fields) data[item.Key] = item.Value.Text.Trim();
        data["camera_path"] = cameraPath.Text.Trim();
        return data;
    }
    Dictionary<string, string> Helper(string action, Dictionary<string, string> payload = null)
    {
        var start = new ProcessStartInfo { FileName = Path.Combine(root, ".venv", "Scripts", "python.exe"),
            Arguments = "\"" + Path.Combine(root, "scripts", "native_config.py") + "\" " + action,
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        using (var process = Process.Start(start))
        {
            if (payload != null) foreach (var pair in payload)
                process.StandardInput.WriteLine(pair.Key + "\t" + Convert.ToBase64String(Encoding.UTF8.GetBytes(pair.Value ?? "")));
            process.StandardInput.Close();
            string output = process.StandardOutput.ReadToEnd(), error = process.StandardError.ReadToEnd();
            if (!process.WaitForExit(45000)) { process.Kill(); throw new Exception("A configuração demorou demais."); }
            var result = new Dictionary<string, string>();
            foreach (string line in output.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
            {
                int tab = line.IndexOf('\t');
                if (tab < 1) continue;
                try { result[line.Substring(0, tab)] = Encoding.UTF8.GetString(Convert.FromBase64String(line.Substring(tab + 1))); }
                catch (FormatException) { }
            }
            if (result.Count == 0) throw new Exception("Falha ao ler a configuração. " + error.Trim());
            if (result.ContainsKey("ok") && result["ok"] != "true")
                throw new Exception(result["message"]);
            return result;
        }
    }
    void Busy(bool value, string message)
    {
        busy = value; save.Enabled = !value; statusSource = message; status.Text = T(message); status.ForeColor = muted;
    }
    void LoadSettings()
    {
        Busy(true, "Carregando configurações…");
        Task.Run(() => Helper("get")).ContinueWith(task => BeginInvoke((Action)(() => {
            if (task.IsFaulted) { Busy(false, task.Exception.GetBaseException().Message); status.ForeColor = Color.Salmon; return; }
            var data = task.Result;
            needsAdmin = data.ContainsKey("needs_admin") && data["needs_admin"] == "true";
            mode.SelectedIndex = Convert.ToString(data["mode"]) == "arena" ? 1 : 0;
            camera.SelectedIndex = Math.Max(0, Array.IndexOf(new[] { "webcam", "rtsp", "auto" }, Convert.ToString(data["camera_mode"])));
            proxy.SelectedIndex = data["proxy_enabled"] == "true" ? 1 : 0;
            language.SelectedIndex = Math.Max(0, Array.IndexOf(localeCodes, Convert.ToString(data["locale"])));
            foreach (string key in new[] { "camera_index", "camera_ip", "camera_port", "camera_user", "proxy_host", "proxy_port", "proxy_user" })
                if (data.ContainsKey(key)) fields[key].Text = Convert.ToString(data[key]);
            if (data.ContainsKey("camera_path")) cameraPath.Text = Convert.ToString(data["camera_path"]);
            restartOnSave = !string.IsNullOrEmpty(data["server_pid"]);
            Visibility(); Busy(false, ""); ApplyLanguage();
        })));
    }
    void Test(string target)
    {
        if (busy) return;
        var data = Values(); data["target"] = target; Busy(true, "Testando conexão…");
        Task.Run(() => Helper("test", data)).ContinueWith(task => BeginInvoke((Action)(() => {
            if (task.IsFaulted) { Busy(false, task.Exception.GetBaseException().Message); status.ForeColor = Color.Salmon; return; }
            Busy(false, Convert.ToString(task.Result["message"]));
            status.ForeColor = task.Result["ok"] == "true" ? Color.LightGreen : Color.Salmon;
        })));
    }
    void Save()
    {
        if (busy) return;
        var data = Values(); Busy(true, "Salvando configurações…");
        Task.Run(() => Helper("save", data)).ContinueWith(task => BeginInvoke((Action)(() => {
            if (task.IsFaulted) { Busy(false, task.Exception.GetBaseException().Message); status.ForeColor = Color.Salmon; return; }
            bool restarting = task.Result["restarting"] == "true";
            int oldPid = string.IsNullOrEmpty(task.Result["old_pid"]) ? -1 : Convert.ToInt32(task.Result["old_pid"]);
            if (!restarting)
            {
                try
                {
                    Process.Start(new ProcessStartInfo { FileName = Path.Combine(root, ".venv", "Scripts", "pythonw.exe"),
                        Arguments = "\"" + Path.Combine(root, "launch_windows.pyw") + "\" --direct",
                        WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true });
                }
                catch (Exception error) { Busy(false, error.Message); status.ForeColor = Color.Salmon; return; }
            }
            statusSource = restarting ? "Reiniciando o RealGuard…" : "Iniciando o RealGuard…";
            status.Text = T(statusSource);
            Task.Run(() => WaitReady(oldPid)).ContinueWith(done => BeginInvoke((Action)(() => {
                if (done.IsFaulted || !done.Result) { Busy(false, "O RealGuard não iniciou. Consulte logs/console.log."); status.ForeColor = Color.Salmon; }
                else Close();
            })));
        })));
    }
    bool WaitReady(int previousPid)
    {
        string file = Path.Combine(root, "logs", "active_server.json");
        for (int attempt = 0; attempt < 120; attempt++)
        {
            try
            {
                string content = File.ReadAllText(file);
                var pidMatch = Regex.Match(content, "\\\"pid\\\"\\s*:\\s*(\\d+)");
                var urlMatch = Regex.Match(content, "\\\"url\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
                if (pidMatch.Success && urlMatch.Success && Convert.ToInt32(pidMatch.Groups[1].Value) != previousPid)
                {
                    var request = (HttpWebRequest)WebRequest.Create(urlMatch.Groups[1].Value + "/api/auth/state");
                    request.Proxy = null; request.Timeout = 900;
                    using (var response = (HttpWebResponse)request.GetResponse())
                        if (response.StatusCode == HttpStatusCode.OK) return true;
                }
            }
            catch { }
            Thread.Sleep(1000);
        }
        return false;
    }
}

internal sealed class NativeSetupI18n
{
    readonly Dictionary<string, string[]> rows = new Dictionary<string, string[]>();

    internal NativeSetupI18n(string root)
    {
        string path = Path.Combine(root, "web", "native-config-i18n.tsv");
        if (!File.Exists(path)) return;
        foreach (string line in File.ReadAllLines(path, Encoding.UTF8))
        {
            string[] cells = line.Split('\t');
            if (cells.Length == 10) rows[cells[0]] = cells;
        }
    }

    internal string Get(int index, string source)
    {
        string[] cells;
        if (index < 1 || index > 9 || !rows.TryGetValue(source, out cells)) return source;
        return cells[index];
    }
}
