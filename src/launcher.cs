using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("DUTY")]
[assembly: AssemblyDescription("Launch DUTY with its local Python runtime")]

internal static class Launcher
{
    [STAThread]
    private static int Main()
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, "local", "runtime", "python", "pythonw.exe");
            string gui = Path.Combine(root, "src", "gui.py");

            if (!File.Exists(gui))
            {
                ShowError("DUTY's src/gui.py is missing. Restore the project files, then complete setup.");
                return 1;
            }
            if (!File.Exists(python))
            {
                ShowError("Complete setup before opening DUTY. From the project folder, run:\n\n"
                    + "python src/main.py setup\n\nUse Windows x64 Python 3.11+ with pip.");
                return 1;
            }

            var start = new ProcessStartInfo
            {
                FileName = python,
                Arguments = "\"" + gui + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            using (Process child = Process.Start(start))
            {
                if (child == null)
                {
                    ShowError("Windows could not start DUTY's local Python.");
                    return 1;
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            ShowError("DUTY could not start.\n\n" + error.Message);
            return 1;
        }
    }

    private static void ShowError(string message)
    {
        MessageBox.Show(message, "DUTY", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
