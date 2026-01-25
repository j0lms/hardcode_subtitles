import tkinter as tk
import frontend

def main():
    root = tk.Tk()
    root.title("Subtitle Overlay Tool — Dark Mode")
    root.geometry("820x680")

    frontend.build_ui(root)

    root.mainloop()

if __name__ == "__main__":
    main()
