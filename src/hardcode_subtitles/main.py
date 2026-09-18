import tkinter as tk

from . import frontend


def make_root() -> tk.Tk:
    try:
        from tkinterdnd2 import TkinterDnD

        return TkinterDnD.Tk()
    except Exception:
        return tk.Tk()


def main():
    root = make_root()
    frontend.build_ui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
