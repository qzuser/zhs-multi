// zhs-multi 一键启动器（编译成 一键刷课.exe）
//
// 双击 = 出菜单，选完就把活儿交给 ..\.venv-zhs\Scripts\python.exe -m app
// 带参数 = 直接透传： 一键刷课.exe status / 一键刷课.exe run -c 1 -j 1
//
// 编译（Windows 自带 .NET Framework 的 csc 就够，不需要装 SDK）：
//   csc /nologo /target:exe /out:"一键刷课.exe" /codepage:65001 /optimize+ tools\launcher\ZhsLauncher.cs
//
// 注意：csc v4.0.30319 只认 C# 5，别用 $"" / ?. / nameof 这些新语法。

using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

[assembly: AssemblyTitle("zhs-multi 一键刷课")]
[assembly: AssemblyDescription("智慧树 / 知到 刷课工具的一键启动入口")]
[assembly: AssemblyCompany("YG")]
[assembly: AssemblyProduct("zhs-multi")]
[assembly: AssemblyCopyright("© 2026 YG")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: AssemblyInformationalVersion("1.0.0")]

internal static class ZhsLauncher
{
    [DllImport("kernel32.dll")]
    private static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll")]
    private static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);

    private static string root;
    private static string pyExe;
    private static string pyPrefix;
    private static bool attached;
    // true = 身边有打包好的 zhs-multi.exe，直接调它，不需要 Python
    private static bool standalone;

    private static int Main(string[] argv)
    {
        try { Console.OutputEncoding = new UTF8Encoding(false); }
        catch { }
        try { Console.Title = "zhs-multi 一键刷课"; }
        catch { }

        root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        attached = StdinIsConsole();

        standalone = FindBundled();

        if (!standalone && !File.Exists(Path.Combine(root, @"app\__main__.py")))
        {
            Line("找不到 app\\__main__.py —— 这个 exe 得和 app\\ 文件夹放在一起（项目根目录）。");
            Line("现在的位置：" + root);
            Pause();
            return 2;
        }

        if (!standalone && !FindPython())
        {
            Line("没找到能用的 Python，身边也没有打包好的 zhs-multi.exe。");
            Line("两个办法：");
            Line(" 1) 用免安装版：把 zhs-multi.exe 和本文件放在同一个文件夹里");
            Line(" 2) 自己建环境：");
            Line(@"      py -3 -m venv ..\.venv-zhs");
            Line(@"      ..\.venv-zhs\Scripts\python.exe -m pip install -e ..\zhs");
            Pause();
            return 2;
        }

        if (argv.Length > 0)
        {
            return Run(Join(argv), false);
        }

        Banner();
        string action = Menu();
        if (action == null)
        {
            return 0;
        }
        return Run(action, true);
    }

    private static bool StdinIsConsole()
    {
        try
        {
            uint mode;
            return GetConsoleMode(GetStdHandle(-10), out mode);
        }
        catch
        {
            return true;
        }
    }

    /// <summary>身边有没有打包好的 zhs-multi.exe（免安装版）</summary>
    private static bool FindBundled()
    {
        string env = Environment.GetEnvironmentVariable("ZHS_MULTI_EXE");
        if (env != null && env.Length > 0 && File.Exists(env))
        {
            pyExe = env;
            pyPrefix = "";
            return true;
        }
        string[] cands = new string[]
        {
            Path.Combine(root, "zhs-multi.exe"),
            Path.Combine(root, @"bin\zhs-multi.exe"),
        };
        for (int i = 0; i < cands.Length; i++)
        {
            if (File.Exists(cands[i]))
            {
                pyExe = cands[i];
                pyPrefix = "";
                return true;
            }
        }
        return false;
    }

    private static bool FindPython()
    {
        string venv = Path.GetFullPath(Path.Combine(root, @"..\.venv-zhs\Scripts\python.exe"));
        if (File.Exists(venv))
        {
            pyExe = venv;
            pyPrefix = "";
            return true;
        }
        string[] names = new string[] { "py", "python" };
        for (int i = 0; i < names.Length; i++)
        {
            string hit = Which(names[i]);
            if (hit != null)
            {
                pyExe = hit;
                pyPrefix = names[i] == "py" ? "-3 " : "";
                return true;
            }
        }
        return false;
    }

    private static string Which(string exe)
    {
        string path = Environment.GetEnvironmentVariable("PATH");
        if (path == null)
        {
            return null;
        }
        string[] dirs = path.Split(';');
        for (int i = 0; i < dirs.Length; i++)
        {
            if (dirs[i].Trim().Length == 0)
            {
                continue;
            }
            try
            {
                string cand = Path.Combine(dirs[i].Trim(), exe + ".exe");
                if (File.Exists(cand))
                {
                    return cand;
                }
            }
            catch { }
        }
        return null;
    }

    private static void Banner()
    {
        Line("");
        Line("============================================================");
        Line("  zhs-multi 一键刷课");
        Line("  项目  ：" + root);
        Line(standalone ? "  解释器：内置（不需要装 Python）" : "  解释器：" + pyExe);
        Line("------------------------------------------------------------");
        Line("  [1] 开刷：没登录会自动出二维码，然后选课开刷");
        Line("  [2] 无人值守：单线程盯梢，撞验证码自动点，刷完自己停");
        Line("  [3] 看状态：登录态、每门课还剩多少、上一轮结果   <- 超时默认");
        Line("  [4] 扫码登录（换账号 / 登录态失效时用）");
        Line("  [5] 环境体检（依赖、网络、登录态）");
        Line("  [6] 只演练不动手（列出要刷的，不改任何进度）");
        Line("  [7] 被验证码拦住 → 自动点掉");
        Line("  [0] 退出");
        Line("------------------------------------------------------------");
        Line("  也是可以直接带参数的：一键刷课.exe run -c 1 -j 1");
        Line("============================================================");
    }

    private static string Menu()
    {
        Line("");
        Console.Write("  选一个（10 秒不选就走 3 = 只看状态）：");
        char pick = ReadKeyWithin(10);
        Line(pick == '\0' ? "3" : pick.ToString());

        switch (pick)
        {
            case '0':
                return null;
            case '1':
                return "";
            case '2':
                return "daemon -c 1 -j 1 --solve-captcha --every 120 --hours 6";
            case '4':
                return "login --terminal-qr";
            case '5':
                return "doctor";
            case '6':
                return "run -c all --dry-run";
            case '7':
                return "captcha";
            default:
                return "status";
        }
    }

    private static char ReadKeyWithin(int seconds)
    {
        if (!attached)
        {
            string line = Console.ReadLine();
            if (string.IsNullOrEmpty(line))
            {
                return '\0';
            }
            line = line.Trim();
            return line.Length > 0 ? line[0] : '\0';
        }
        int waited = 0;
        while (waited < seconds * 100)
        {
            if (Console.KeyAvailable)
            {
                ConsoleKeyInfo k = Console.ReadKey(true);
                return k.KeyChar;
            }
            Thread.Sleep(100);
            waited++;
        }
        return '\0';
    }

    private static int Run(string appArgs, bool keepOpen)
    {
        string args = standalone ? appArgs : (pyPrefix + "-m app");
        if (!standalone && appArgs.Length > 0)
        {
            args += " " + appArgs;
        }
        else if (standalone && appArgs.Length > 0)
        {
            args = appArgs;
        }

        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = pyExe;
        psi.Arguments = args;
        psi.WorkingDirectory = root;
        psi.UseShellExecute = false;
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";

        Line("");
        Line("> " + Path.GetFileName(pyExe) + " " + args);
        Line("");

        int code = 0;
        try
        {
            using (Process p = Process.Start(psi))
            {
                p.WaitForExit();
                code = p.ExitCode;
            }
        }
        catch (Exception ex)
        {
            Line("启动失败：" + ex.Message);
            code = 2;
        }

        if (keepOpen)
        {
            Line("");
            Line("退出码 " + code + "（0=正常  2=没任务/没登录  3=平台挡住  1=用法错）");
            Pause();
        }
        return code;
    }

    private static string Join(string[] parts)
    {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < parts.Length; i++)
        {
            if (i > 0)
            {
                sb.Append(' ');
            }
            string a = parts[i];
            if (a.IndexOf(' ') >= 0)
            {
                sb.Append('"').Append(a).Append('"');
            }
            else
            {
                sb.Append(a);
            }
        }
        return sb.ToString();
    }

    private static void Pause()
    {
        if (!attached)
        {
            return;
        }
        Line("");
        Console.Write("按任意键关闭…");
        try { Console.ReadKey(true); }
        catch { }
        Line("");
    }

    private static void Line(string text)
    {
        Console.WriteLine(text);
    }
}
