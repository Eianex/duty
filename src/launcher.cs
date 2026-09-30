using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

[assembly: AssemblyTitle("DUTY")]
[assembly: AssemblyDescription("Launch and prepare DUTY with its local Python runtime")]

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

            string detail;
            if (IsReady(root, main, python, pythonw, out detail))
            {
                LaunchGui(root, gui, pythonw);
                return 0;
            }

            using (SetupWindow window = new SetupWindow(root, main, gui, python, pythonw))
            {
                Application.Run(window);
                return window.ExitCode;
            }
        }
        catch (Exception error)
        {
            ShowError("DUTY could not start.\n\n" + error.Message);
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
            CommandResult result = RunCommand(python, "-B " + Quote(main) + " --json setup --check",
                                              root, 15000, null);
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

    internal static string FindExternalPython(string root, string portable)
    {
        List<string> candidates = new List<string>();
        HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        try
        {
            CommandResult installed = RunCommand("py.exe", "-0p", root, 5000, null);
            foreach (string line in installed.Output.Split('\n'))
            {
                Match match = Regex.Match(line, @"([A-Za-z]:\\.*?python(?:3)?\.exe)", RegexOptions.IgnoreCase);
                if (match.Success)
                    AddCandidate(candidates, seen, match.Groups[1].Value);
            }
        }
        catch (Exception) { /* The Python launcher is optional. */ }

        string path = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (string entry in path.Split(Path.PathSeparator))
        {
            string folder = Environment.ExpandEnvironmentVariables(entry.Trim().Trim('"'));
            if (folder.Length == 0)
                continue;
            try
            {
                AddCandidate(candidates, seen, Path.Combine(folder, "python.exe"));
                AddCandidate(candidates, seen, Path.Combine(folder, "python3.exe"));
            }
            catch (ArgumentException) { }
        }

        foreach (string candidate in candidates)
        {
            if (string.Equals(Path.GetFullPath(candidate), Path.GetFullPath(portable),
                              StringComparison.OrdinalIgnoreCase))
                continue;
            try
            {
                const string probe = "import sys,sysconfig,importlib.metadata as m; "
                    + "v=tuple(int(x) for x in m.version('pip').split('.')[:2]); "
                    + "sys.exit(0 if sys.version_info[:2]>=(3,11) and "
                    + "sysconfig.get_platform()=='win-amd64' and v>=(22,3) else 1)";
                CommandResult identity = RunCommand(candidate, "-c " + Quote(probe), root, 10000, null);
                if (identity.ExitCode == 0 &&
                    RunCommand(candidate, "-m pip --version", root, 10000, null).ExitCode == 0)
                    return candidate;
            }
            catch (Exception) { /* Try the next installed interpreter. */ }
        }
        return null;
    }

    private static void AddCandidate(List<string> candidates, HashSet<string> seen, string path)
    {
        try
        {
            string full = Path.GetFullPath(path.Trim().Trim('"'));
            if (File.Exists(full) && seen.Add(full))
                candidates.Add(full);
        }
        catch (Exception) { /* Ignore malformed PATH entries. */ }
    }

    private static void ShowError(string message)
    {
        MessageBox.Show(message, "DUTY", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}

internal sealed class SetupWindow : Form
{
    private readonly string root, main, gui, python, pythonw, logPath;
    private readonly BackgroundWorker worker;
    private readonly Label status;
    private readonly TextBox details;
    private readonly ProgressBar progress;
    internal int ExitCode = 1;

    internal SetupWindow(string root, string main, string gui, string python, string pythonw)
    {
        this.root = root;
        this.main = main;
        this.gui = gui;
        this.python = python;
        this.pythonw = pythonw;
        logPath = Path.Combine(root, "local", "logs", "launcher-setup.log");
        Text = "DUTY setup";
        Width = 620;
        Height = 350;
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        ControlBox = false;

        TableLayoutPanel layout = new TableLayoutPanel();
        layout.Dock = DockStyle.Fill;
        layout.Padding = new Padding(14);
        layout.ColumnCount = 1;
        layout.RowCount = 3;
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 42));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 28));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        status = new Label { Dock = DockStyle.Fill, Text = "Checking the setup requirements…" };
        progress = new ProgressBar { Dock = DockStyle.Fill, Style = ProgressBarStyle.Marquee };
        details = new TextBox { Dock = DockStyle.Fill, Multiline = true, ReadOnly = true,
                                ScrollBars = ScrollBars.Vertical };
        layout.Controls.Add(status, 0, 0);
        layout.Controls.Add(progress, 0, 1);
        layout.Controls.Add(details, 0, 2);
        Controls.Add(layout);

        worker = new BackgroundWorker();
        worker.WorkerReportsProgress = true;
        worker.DoWork += Install;
        worker.ProgressChanged += ShowProgress;
        worker.RunWorkerCompleted += Finish;
        Shown += delegate { worker.RunWorkerAsync(); };
    }

    private void Install(object sender, DoWorkEventArgs e)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(logPath));
        using (StreamWriter log = new StreamWriter(logPath, true, Encoding.UTF8))
        {
            log.AutoFlush = true;
            log.WriteLine("DUTY setup started " + DateTime.Now.ToString("u"));
            string external = Launcher.FindExternalPython(root, python);
            if (external == null)
                throw new InvalidOperationException(
                    "Install Windows x64 Python 3.11+ with pip 22.3+, then double-click DUTY.exe again. "
                    + "You can also run: python src/main.py setup");
            worker.ReportProgress(0, "Using installed Python to prepare DUTY…");
            CommandResult result = Launcher.RunCommand(
                external, Launcher.Quote(main) + " --json setup --launcher-progress", root, -1,
                delegate(string line)
                {
                    lock (log) { log.WriteLine(line); }
                    worker.ReportProgress(0, line);
                });
            if (result.ExitCode != 0)
                throw new InvalidOperationException("Setup failed (exit code " + result.ExitCode
                                                    + "). See the details below.");
            worker.ReportProgress(0, "Checking the installed tools…");
            string detail;
            if (!Launcher.IsReady(root, main, python, pythonw, out detail))
            {
                log.WriteLine(detail);
                throw new InvalidOperationException("Setup finished, but local components are still incomplete. "
                                                    + "See the setup log for details.");
            }
        }
    }

    private void ShowProgress(object sender, ProgressChangedEventArgs e)
    {
        string line = e.UserState as string;
        if (string.IsNullOrWhiteSpace(line))
            return;
        if (line.StartsWith("DUTY_SETUP\t", StringComparison.Ordinal))
        {
            line = line.Substring("DUTY_SETUP\t".Length);
            status.Text = line;
        }
        if (details.TextLength > 24000)
            details.Text = details.Text.Substring(details.TextLength - 16000);
        details.AppendText(line + Environment.NewLine);
    }

    private void Finish(object sender, RunWorkerCompletedEventArgs e)
    {
        if (e.Error != null)
        {
            status.Text = e.Error.Message;
            progress.Style = ProgressBarStyle.Blocks;
            details.AppendText(Environment.NewLine + "Setup log: " + logPath + Environment.NewLine);
            ControlBox = true;
            return;
        }
        try
        {
            Launcher.LaunchGui(root, gui, pythonw);
            ExitCode = 0;
            Close();
        }
        catch (Exception error)
        {
            status.Text = "DUTY was installed, but the window could not start: " + error.Message;
            progress.Style = ProgressBarStyle.Blocks;
            ControlBox = true;
        }
    }
}
