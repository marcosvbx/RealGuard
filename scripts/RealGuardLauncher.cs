using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class RealGuardLauncher
{
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

    [STAThread]
    private static void Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python))
        {
            MessageBox.Show("A instalação do RealGuard está incompleta: Python não encontrado.",
                "RealGuard", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        // Identidade própria para a janela de configuração na barra de tarefas.
        SetCurrentProcessExplicitAppUserModelID("RealGuard.Configuration");
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new NativeSetupForm(root));
    }
}
