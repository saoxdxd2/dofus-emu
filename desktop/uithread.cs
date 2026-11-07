// uithread.cs - the one place that knows which thread owns the UI.
//
// Why this exists: WebView2 requires an STA thread, and `CoreWebView2Environment
// .CreateAsync` fails with RPC_E_CHANGED_MODE (0x80010106) if it is called from
// an MTA thread. The backend used to dispatch form creation through
// `Application.OpenForms[0]`, which is whatever form happens to be first --
// and `InvokeRequired` returns false for a control whose handle is not created
// yet, so the action could run inline on a thread-pool thread. That is exactly
// how a client window ends up being built on MTA and refusing to initialise.
//
// Capturing the real UI SynchronizationContext once, before Application.Run,
// removes the guesswork: Post() always lands on the thread that owns the message
// loop, and there is a single obvious place to look when it does not.

using System;
using System.Threading;
using System.Windows.Forms;

namespace DofusFarm.UI
{
    public static class UiThread
    {
        private static SynchronizationContext _context;
        private static Form _main;

        /// <summary>Call once, on the UI thread, immediately before Application.Run.</summary>
        public static void Capture()
        {
            _context = SynchronizationContext.Current;
            if (_context == null)
            {
                try { _context = new WindowsFormsSynchronizationContext(); } catch { }
            }
        }

        public static void SetMainForm(Form form)
        {
            _main = form;
        }

        public static bool OnUiThread
        {
            get
            {
                if (_main != null && _main.IsHandleCreated) return !_main.InvokeRequired;
                return _context != null && SynchronizationContext.Current == _context;
            }
        }

        /// <summary>Run on the UI thread. Executes inline when already there.</summary>
        public static void Post(Action action)
        {
            if (action == null) return;

            if (OnUiThread)
            {
                action();
                return;
            }

            if (_context != null)
            {
                _context.Post(delegate { action(); }, null);
                return;
            }

            if (_main != null && _main.IsHandleCreated)
            {
                try { _main.BeginInvoke(action); return; } catch { }
            }

            // Last resort: no context, no handle. Run it where we are and let the
            // caller see the failure rather than silently dropping the action.
            action();
        }

        /// <summary>Run on the UI thread and wait. Only for short, non-nested work.</summary>
        public static void Send(Action action)
        {
            if (action == null) return;
            if (OnUiThread) { action(); return; }
            if (_main != null && _main.IsHandleCreated)
            {
                try { _main.Invoke(action); return; } catch { }
            }
            Post(action);
        }

        /// <summary>Diagnostic string for logs: which apartment is this thread in?</summary>
        public static string Apartment()
        {
            try { return Thread.CurrentThread.GetApartmentState().ToString(); }
            catch { return "unknown"; }
        }
    }
}
