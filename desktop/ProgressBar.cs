using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

public class SmoothProgressBar : Control
{
    private readonly Color _from;
    private readonly Color _to;
    private int _value;

    public int Value
    {
        get { return _value; }
        set
        {
            int clamped = Math.Min(100, Math.Max(0, value));
            if (_value != clamped)
            {
                _value = clamped;
                Invalidate();
            }
        }
    }

    public SmoothProgressBar(int height, Color from, Color to)
    {
        _from = from;
        _to = to;
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint
                 | ControlStyles.OptimizedDoubleBuffer, true);
        Height = height;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        if (Width <= 4 || Height <= 4) return;
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        Rectangle rect = new Rectangle(0, 0, Width - 1, Height - 1);
        using (SolidBrush bg = new SolidBrush(Color.FromArgb(26, 28, 36)))
        using (Pen border = new Pen(Color.FromArgb(45, 48, 62), 1f))
        {
            g.FillRectangle(bg, rect);
            g.DrawRectangle(border, rect);
        }
        if (_value <= 0) return;
        int fillW = Math.Min(Width - 2,
                             Math.Max(4, (int)Math.Round(((Width - 2) * _value) / 100.0)));
        Rectangle fill = new Rectangle(1, 1, fillW, Math.Max(2, Height - 2));
        using (LinearGradientBrush grad = new LinearGradientBrush(
            fill, _from, _to, LinearGradientMode.Horizontal))
        {
            g.FillRectangle(grad, fill);
        }
    }
}
