using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using Microsoft.Win32.SafeHandles;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (args.Length == 1 && args[0] == "--verify")
        {
            try { Payload.Verify(Application.ExecutablePath); Environment.Exit(0); }
            catch { Environment.Exit(1); }
            return;
        }
        Application.Run(new InstallerForm());
    }
}

internal sealed class InstallerForm : Form
{
    private readonly Color navy = Color.FromArgb(7, 19, 37);
    private readonly Color panel = Color.FromArgb(13, 30, 54);
    private readonly Color ink = Color.FromArgb(235, 241, 249);
    private readonly Color muted = Color.FromArgb(157, 177, 201);
    private readonly Color gold = Color.FromArgb(221, 177, 87);
    private readonly TextBox location = new TextBox();
    private readonly Button browse = new Button();
    private readonly Button install = new Button();
    private readonly Button open = new Button();
    private readonly CheckBox shortcuts = new CheckBox();
    private readonly ProgressBar progress = new ProgressBar();
    private readonly Label status = new Label();
    private readonly BackgroundWorker worker = new BackgroundWorker();
    private string installedPath = "";

    internal InstallerForm()
    {
        Text = "Instalar RealGuard";
        Size = new Size(650, 480);
        MinimumSize = MaximumSize = Size;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = navy;
        ForeColor = ink;
        Font = new Font("Segoe UI", 10f);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

        var logo = new PictureBox { Bounds = new Rectangle(30, 26, 62, 62), SizeMode = PictureBoxSizeMode.Zoom };
        using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("BrandLogo"))
            if (stream != null) logo.Image = Image.FromStream(stream);
        Controls.Add(logo);
        AddLabel("REALGUARD", 107, 27, 470, 35, 22, true, ink);
        AddLabel("Instalação automática para Windows 64 bits", 109, 63, 470, 25, 10, false, muted);

        var rule = new Panel { Bounds = new Rectangle(30, 105, 584, 1), BackColor = Color.FromArgb(41, 63, 86) };
        Controls.Add(rule);
        AddLabel("Onde deseja instalar?", 30, 128, 560, 25, 11, true, ink);

        location.Bounds = new Rectangle(30, 160, 450, 34);
        location.Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "RealGuard");
        location.BackColor = panel;
        location.ForeColor = ink;
        location.BorderStyle = BorderStyle.FixedSingle;
        Controls.Add(location);

        browse.Bounds = new Rectangle(492, 158, 122, 37);
        browse.Text = "Escolher…";
        StyleButton(browse, panel, ink);
        browse.Click += delegate
        {
            using (var dialog = new FolderBrowserDialog())
            {
                dialog.Description = "Selecione a pasta onde o RealGuard será instalado";
                dialog.SelectedPath = Directory.Exists(location.Text) ? location.Text :
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
                if (dialog.ShowDialog(this) == DialogResult.OK) location.Text = dialog.SelectedPath;
            }
        };
        Controls.Add(browse);

        shortcuts.Bounds = new Rectangle(30, 217, 560, 27);
        shortcuts.Text = "Criar atalhos na área de trabalho e no menu Iniciar";
        shortcuts.Checked = true;
        shortcuts.ForeColor = muted;
        shortcuts.BackColor = navy;
        Controls.Add(shortcuts);
        AddLabel("Python, modelos e bibliotecas incluídos. Não é preciso baixar arquivos.", 30, 255, 570, 26, 9, false, muted);

        progress.Bounds = new Rectangle(30, 305, 584, 18);
        progress.Style = ProgressBarStyle.Continuous;
        Controls.Add(progress);
        status.Bounds = new Rectangle(30, 330, 584, 38);
        status.ForeColor = muted;
        status.Text = "Pronto para instalar.";
        Controls.Add(status);

        install.Bounds = new Rectangle(457, 383, 157, 45);
        install.Text = "Instalar";
        StyleButton(install, gold, navy);
        install.Font = new Font("Segoe UI", 10f, FontStyle.Bold);
        install.Click += InstallClicked;
        Controls.Add(install);
        open.Bounds = new Rectangle(30, 383, 190, 45);
        open.Text = "Abrir RealGuard";
        open.Visible = false;
        StyleButton(open, panel, ink);
        open.Click += delegate { Process.Start(Path.Combine(installedPath, "RealGuard.exe")); Close(); };
        Controls.Add(open);

        worker.WorkerReportsProgress = true;
        worker.DoWork += delegate(object sender, DoWorkEventArgs e)
        {
            object[] options = (object[])e.Argument;
            string target = (string)options[0];
            Payload.Install(Application.ExecutablePath, target, (bool)options[1],
                delegate(int percent, string message) { worker.ReportProgress(percent, message); });
            e.Result = target;
        };
        worker.ProgressChanged += delegate(object sender, ProgressChangedEventArgs e)
        {
            progress.Value = Math.Max(0, Math.Min(100, e.ProgressPercentage));
            status.Text = Convert.ToString(e.UserState);
        };
        worker.RunWorkerCompleted += delegate(object sender, RunWorkerCompletedEventArgs e)
        {
            if (e.Error != null)
            {
                status.Text = "A instalação não foi concluída: " + e.Error.Message;
                status.ForeColor = Color.FromArgb(255, 151, 151);
                MessageBox.Show(this, e.Error.Message + "\n\nVocê pode executar o instalador novamente.",
                    "RealGuard — instalação interrompida", MessageBoxButtons.OK, MessageBoxIcon.Error);
                install.Enabled = true;
                browse.Enabled = location.Enabled = shortcuts.Enabled = true;
                return;
            }
            installedPath = (string)e.Result;
            progress.Value = 100;
            status.ForeColor = Color.FromArgb(117, 224, 166);
            status.Text = "Instalação concluída. O RealGuard está pronto para abrir.";
            install.Visible = false;
            open.Visible = true;
        };
        FormClosing += delegate(object sender, FormClosingEventArgs e)
        {
            if (worker.IsBusy)
            {
                e.Cancel = true;
                MessageBox.Show(this, "Aguarde o término da instalação.", "RealGuard",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        };
    }

    private Label AddLabel(string text, int x, int y, int w, int h, float size, bool bold, Color color)
    {
        var label = new Label { Text = text, Bounds = new Rectangle(x, y, w, h), ForeColor = color,
            BackColor = navy, Font = new Font("Segoe UI", size, bold ? FontStyle.Bold : FontStyle.Regular) };
        Controls.Add(label);
        return label;
    }

    private static void StyleButton(Button button, Color background, Color foreground)
    {
        button.BackColor = background;
        button.ForeColor = foreground;
        button.FlatStyle = FlatStyle.Flat;
        button.FlatAppearance.BorderColor = Color.FromArgb(50, 75, 100);
        button.Cursor = Cursors.Hand;
    }

    private void InstallClicked(object sender, EventArgs e)
    {
        string target;
        try
        {
            target = Path.GetFullPath(location.Text.Trim()).TrimEnd(Path.DirectorySeparatorChar);
            if (target.Length < 4 || Path.GetPathRoot(target).TrimEnd(Path.DirectorySeparatorChar).Equals(target,
                    StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Escolha uma pasta, não a raiz do disco.");
            Payload.Verify(Application.ExecutablePath);
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, "RealGuard", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        location.Text = target;
        install.Enabled = browse.Enabled = location.Enabled = shortcuts.Enabled = false;
        status.Text = "Preparando instalação…";
        worker.RunWorkerAsync(new object[] { target, shortcuts.Checked });
    }
}

internal static class Payload
{
    private static readonly byte[] Magic = Encoding.ASCII.GetBytes("RGPKG001");
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateDirectoryW(string path, IntPtr securityAttributes);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string name, uint access, uint share,
        IntPtr securityAttributes, uint disposition, uint flags, IntPtr template);

    internal static void Verify(string executable)
    {
        using (var file = File.Open(executable, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        {
            if (file.Length < 24) throw new InvalidDataException("Instalador sem pacote de arquivos.");
            file.Seek(-24, SeekOrigin.End);
            var footer = new byte[24];
            if (file.Read(footer, 0, footer.Length) != footer.Length)
                throw new InvalidDataException("Pacote de instalação incompleto.");
            for (int i = 0; i < 8; i++)
                if (footer[i] != Magic[i]) throw new InvalidDataException("Pacote de instalação inválido.");
            long offset = BitConverter.ToInt64(footer, 8);
            long length = BitConverter.ToInt64(footer, 16);
            if (offset <= 0 || length <= 0 || offset + length != file.Length - 24)
                throw new InvalidDataException("Pacote de instalação corrompido.");
        }
    }

    internal static void Install(string executable, string target, bool makeShortcuts, Action<int, string> update)
    {
        Verify(executable);
        if (!Environment.Is64BitOperatingSystem)
            throw new InvalidOperationException("Este instalador requer Windows de 64 bits.");
        CheckRunning(target);
        var drive = new DriveInfo(Path.GetPathRoot(target));
        long required = Directory.Exists(Path.Combine(target, ".venv")) ? 1800000000L : 1100000000L;
        if (drive.AvailableFreeSpace < required)
            throw new IOException("Espaço insuficiente. Libere ao menos " + (required / 1000000000.0).ToString("0.0") + " GB no disco.");
        Directory.CreateDirectory(target);
        string archivePath = Path.Combine(target, ".realguard-payload-" + Guid.NewGuid().ToString("N") + ".zip");
        string stageVenv = Path.Combine(target, ".venv-installing-" + Guid.NewGuid().ToString("N"));
        try
        {
            update(2, "Lendo os arquivos incluídos…");
            CopyArchive(executable, archivePath);
            using (var archive = ZipFile.OpenRead(archivePath))
            {
                int files = archive.Entries.Count;
                int done = 0;
                foreach (var entry in archive.Entries)
                {
                    if (entry.FullName.StartsWith("project/", StringComparison.Ordinal))
                        Extract(entry, target, entry.FullName.Substring(8));
                    done++;
                    if (done % 100 == 0) update(8 + done * 35 / files, "Copiando o RealGuard e os modelos…");
                }
                update(45, "Preparando o Python local…");
                Directory.CreateDirectory(Path.Combine(target, "data"));
                Directory.CreateDirectory(Path.Combine(target, "data", "events"));
                Directory.CreateDirectory(Path.Combine(target, "data", "backups"));
                Directory.CreateDirectory(Path.Combine(target, "logs"));
                string runtime = Path.Combine(target, "runtime", "python.exe");
                if (!File.Exists(runtime) || !File.Exists(Path.Combine(target, "RealGuard.exe")))
                    throw new InvalidDataException("Arquivos essenciais ausentes no pacote.");
                Run(runtime, "-m venv --without-pip " + Quote(stageVenv), target);
                done = 0;
                foreach (var entry in archive.Entries)
                {
                    if (entry.FullName.StartsWith("packages/", StringComparison.Ordinal))
                        Extract(entry, Path.Combine(stageVenv, "Lib", "site-packages"), entry.FullName.Substring(9));
                    done++;
                    if (done % 100 == 0) update(47 + done * 43 / files, "Instalando as bibliotecas de IA sem usar a internet…");
                }
            }
            update(91, "Verificando as bibliotecas…");
            Run(Path.Combine(stageVenv, "Scripts", "python.exe"),
                "-c \"import cv2,numpy,onnxruntime,insightface,fastapi,uvicorn; print('OK')\"", target);
            string active = Path.Combine(target, ".venv");
            string previous = Path.Combine(target, ".venv-previous-" + Guid.NewGuid().ToString("N"));
            if (Directory.Exists(active)) Directory.Move(active, previous);
            try { Directory.Move(stageVenv, active); }
            catch
            {
                if (Directory.Exists(previous)) Directory.Move(previous, active);
                throw;
            }
            update(96, "Criando atalhos…");
            if (makeShortcuts) CreateShortcuts(target);
            File.WriteAllText(Path.Combine(target, ".realguard-installer.json"),
                "{\"product\":\"RealGuard\",\"build\":\"" + DateTime.Now.ToString("yyyy-MM-dd HH:mm") +
                "\",\"complete\":true}", Encoding.UTF8);
            if (Directory.Exists(previous))
            {
                try { Directory.Delete(previous, true); } catch { }
            }
            update(100, "Instalação concluída.");
        }
        finally
        {
            try { if (File.Exists(archivePath)) File.Delete(archivePath); } catch { }
            try { if (Directory.Exists(stageVenv)) Directory.Delete(stageVenv, true); } catch { }
        }
    }

    private static void CheckRunning(string target)
    {
        string file = Path.Combine(target, "logs", "active_server.json");
        if (!File.Exists(file)) return;
        try
        {
            var match = Regex.Match(File.ReadAllText(file), "\\\"pid\\\"\\s*:\\s*(\\d+)");
            if (!match.Success) return;
            using (Process process = Process.GetProcessById(int.Parse(match.Groups[1].Value)))
                if (!process.HasExited)
                    throw new InvalidOperationException("Encerre a sessão do RealGuard antes de atualizar esta instalação.");
        }
        catch (ArgumentException) { }
    }

    private static void CopyArchive(string executable, string destination)
    {
        using (var source = File.Open(executable, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        using (var output = File.Create(destination))
        {
            source.Seek(-24, SeekOrigin.End);
            var footer = new byte[24];
            source.Read(footer, 0, 24);
            long offset = BitConverter.ToInt64(footer, 8);
            long remaining = BitConverter.ToInt64(footer, 16);
            source.Seek(offset, SeekOrigin.Begin);
            var buffer = new byte[1024 * 1024];
            while (remaining > 0)
            {
                int read = source.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                if (read == 0) throw new EndOfStreamException("Pacote de instalação incompleto.");
                output.Write(buffer, 0, read);
                remaining -= read;
            }
        }
    }

    private static void Extract(ZipArchiveEntry entry, string root, string relative)
    {
        if (String.IsNullOrEmpty(relative) || relative.EndsWith("/")) return;
        string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        string[] parts = relative.Split('/');
        foreach (string part in parts)
            if (String.IsNullOrEmpty(part) || part == "." || part == ".." || part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
                throw new InvalidDataException("Caminho inválido no pacote.");
        string destination = fullRoot + String.Join(Path.DirectorySeparatorChar.ToString(), parts);
        string extended = "\\\\?\\" + destination;
        CreateDirectoryLong(destination.Substring(0, destination.LastIndexOf(Path.DirectorySeparatorChar)));
        using (var input = entry.Open())
        using (var handle = CreateFileW(extended, 0x40000000, 0, IntPtr.Zero, 2, 0x80, IntPtr.Zero))
        {
            if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "Não foi possível criar " + relative);
            using (var output = new FileStream(handle, FileAccess.Write)) input.CopyTo(output);
        }
    }

    private static void CreateDirectoryLong(string path)
    {
        if (path.Length < 220)
        {
            Directory.CreateDirectory(path);
            return;
        }
        string parent = path.Substring(0, path.LastIndexOf(Path.DirectorySeparatorChar));
        if (!String.Equals(parent, path, StringComparison.OrdinalIgnoreCase)) CreateDirectoryLong(parent);
        if (!CreateDirectoryW("\\\\?\\" + path, IntPtr.Zero) && Marshal.GetLastWin32Error() != 183)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Não foi possível criar a pasta " + path);
    }

    private static void Run(string executable, string arguments, string workdir)
    {
        var start = new ProcessStartInfo(executable, arguments) { WorkingDirectory = workdir,
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true,
            RedirectStandardError = true };
        using (var process = Process.Start(start))
        {
            string output = process.StandardOutput.ReadToEnd();
            string error = process.StandardError.ReadToEnd();
            process.WaitForExit();
            if (process.ExitCode != 0)
                throw new InvalidOperationException("Falha ao preparar o Python local: " +
                    (String.IsNullOrWhiteSpace(error) ? output : error).Trim());
        }
    }

    private static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }

    private static void CreateShortcuts(string target)
    {
        string exe = Path.Combine(target, "RealGuard.exe");
        string icon = Path.Combine(target, "web", "brand", "realguard-config.ico") + ",0";
        string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        string menu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "RealGuard");
        Directory.CreateDirectory(menu);
        CreateShortcut(Path.Combine(desktop, "RealGuard.lnk"), exe, target, icon);
        CreateShortcut(Path.Combine(menu, "RealGuard.lnk"), exe, target, icon);
    }

    private static void CreateShortcut(string path, string exe, string target, string icon)
    {
        Type type = Type.GetTypeFromProgID("WScript.Shell");
        if (type == null) throw new InvalidOperationException("Não foi possível criar o atalho do Windows.");
        object shell = Activator.CreateInstance(type);
        object link = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
        try
        {
            Type linkType = link.GetType();
            linkType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, link, new object[] { exe });
            linkType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, link, new object[] { target });
            linkType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, link, new object[] { icon });
            linkType.InvokeMember("Description", BindingFlags.SetProperty, null, link, new object[] { "RealGuard" });
            linkType.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, new object[0]);
        }
        finally
        {
            Marshal.ReleaseComObject(link);
            Marshal.ReleaseComObject(shell);
        }
    }
}
