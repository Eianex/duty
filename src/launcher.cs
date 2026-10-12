using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("DUTY")]
[assembly: AssemblyDescription("Launch DUTY with its bundled Python runtime")]

internal sealed class CommandResult
{
    internal int ExitCode;
    internal string Output;
}

internal static class Launcher
{
    [STAThread]
    private static int Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string main = Path.Combine(root, "src", "main.py");
            string gui = Path.Combine(root, "src", "gui.py");
            string python = Path.Combine(root, "local", "runtime", "python", "python.exe");
            string pythonw = Path.Combine(root, "local", "runtime", "python", "pythonw.exe");
            if (!File.Exists(main) || !File.Exists(gui))
            {
                ShowError("DUTY's Python source files are missing. Restore the project files.");
                return 1;
            }

            foreach (string file in new string[] { python, pythonw })
            {
                if (File.Exists(file) && new FileInfo(file).Length < 1024)
                {
                    using (StreamReader reader = new StreamReader(file))
                    {
                        if ((reader.ReadLine() ?? "").StartsWith("version https://git-lfs.github.com/spec/v1"))
                            throw new InvalidOperationException("Git LFS binary files are missing. For a Git clone, install Git LFS and run git lfs pull.");
                    }
                }
            }
            string detail;
            if (!IsReady(root, main, python, pythonw, out detail))
                throw new InvalidOperationException("The DUTY bundle is incomplete or incompatible.\n\n" + detail);
            LaunchGui(root, gui, pythonw);
            return 0;
        }
        catch (Exception error)
        {
            ShowError("DUTY could not start.\n\n" + error.Message + "\n\nRestore the complete DUTY download. No Python installation or setup is needed.");
            return 1;
        }
    }

    internal static string Quote(string value)
    {
        return "\"" + value + "\"";
    }

    internal static void LaunchGui(string root, string gui, string pythonw)
    {
        ProcessStartInfo start = new ProcessStartInfo
        {
            FileName = pythonw,
            Arguments = Quote(gui),
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        using (Process child = Process.Start(start))
        {
            if (child == null)
                throw new InvalidOperationException("Windows could not start DUTY's local Python.");
        }
    }

    internal static bool IsReady(string root, string main, string python, string pythonw, out string detail)
    {
        detail = "Local Python is missing.";
        if (!File.Exists(python) || !File.Exists(pythonw))
            return false;
        try
        {
            CommandResult result = RunCommand(python, "-B " + Quote(main) + " --json status --check",
                                              root, 30000, null);
            detail = result.Output;
            return result.ExitCode == 0;
        }
        catch (Exception error)
        {
            detail = error.Message;
            return false;
        }
    }

    internal static CommandResult RunCommand(string executable, string arguments, string root,
                                             int timeoutMilliseconds, Action<string> onLine)
    {
        ProcessStartInfo start = new ProcessStartInfo
        {
            FileName = executable,
            Arguments = arguments,
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        start.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
        StringBuilder output = new StringBuilder();
        DataReceivedEventHandler collect = delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data == null)
                return;
            lock (output)
            {
                output.AppendLine(e.Data);
                if (output.Length > 80000)
                    output.Remove(0, output.Length - 40000);
            }
            if (onLine != null)
                onLine(e.Data);
        };
        using (Process process = new Process())
        {
            process.StartInfo = start;
            process.OutputDataReceived += collect;
            process.ErrorDataReceived += collect;
            if (!process.Start())
                throw new InvalidOperationException("Windows could not start " + executable);
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            if (!process.WaitForExit(timeoutMilliseconds))
            {
                try { process.Kill(); }
                catch (InvalidOperationException) { }
                process.WaitForExit();
                throw new TimeoutException("Timed out while running " + executable);
            }
            process.WaitForExit();
            return new CommandResult { ExitCode = process.ExitCode, Output = output.ToString() };
        }
    }

    private static void ShowError(string message)
    {
        MessageBox.Show(message, "DUTY", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}

