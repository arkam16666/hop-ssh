<div align="center">

<img src="data/hop.svg" width="96" alt="Hop icon">

# ⇢ hop ✿

**A cozy SSH manager for Linux: terminal, files, live dashboard and tunnels in one window.**

GTK4 · libadwaita · Python · no sudo needed to install

[English](#features) · [ภาษาไทย](#ภาษาไทย)

</div>

---

## Features

- 🗂️ **Server cards**: online/offline status with latency, OS logo detected automatically, groups and notes
- 🖥️ **Built-in terminal**: every server opens as a tab, no separate terminal window. Colors follow your terminal (Ptyxis) palette
- 📁 **File manager over SFTP**: browse, upload/download files *and folders*, drag & drop from your file manager, rename, delete, new folder, with a progress bar and speed
- ✏️ **Edit remote files**: double-click a text file to open it in a syntax-highlighted editor and save back with `Ctrl+S`
- 📊 **Live dashboard**: CPU / RAM / network graphs, disks, uptime, load and top processes, updated every 2 s
- 🪟 **Split view**: terminal and files side by side; the terminal `cd`s to whatever folder you open
- ⭐ **Snippets**: save the commands you use all the time and run them with one click
- 🚇 **Port forwarding**: open a web app or database running on the server at `localhost` with a switch
- 📡 **Broadcast**: type one command and send it to several servers at once (`Ctrl+Shift+B`)
- ⌨️ **Terminal mode too**: `hop` gives you an fzf picker with live status and a preview, right in your shell

Your servers live in plain **`~/.ssh/config`**, so `ssh <name>` keeps working everywhere and nothing is locked into Hop.
Every write keeps a backup at `~/.ssh/config.hop-bak`.

## Install

```bash
git clone https://github.com/arkam16666/hop-ssh.git
cd hop-ssh
./install.sh
```

Then open **Hop** from your app menu, or run `hop` in a terminal.

Requirements (preinstalled on Ubuntu/Fedora GNOME): Python 3, GTK 4, libadwaita ≥ 1.5, OpenSSH, `gvfs-backends` (SFTP).
Optional: `fzf` for the terminal picker.
The installer fetches the GTK4 VTE / GtkSourceView Python bindings and the fonts (Nunito, Mali, JetBrainsMono Nerd Font) into your home directory if they're missing.

## Terminal usage

```text
hop               pick a host (fzf), Enter connects
hop <name>        connect straight away
hop ls            list hosts with status
hop add           add a host (asks step by step)
hop edit <name>   edit a host
hop rm <name>     delete a host
hop key <name>    copy your SSH key to the host (no more passwords)
hop gui           open the window
```

## Keyboard shortcuts (window)

| Keys | Action |
|---|---|
| `Ctrl+N` | Add server |
| `Ctrl+F` | Search servers |
| `Ctrl+R` | Refresh status |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | Copy / paste in terminal |
| `Ctrl +` / `Ctrl −` / `Ctrl 0` | Terminal zoom |
| `Ctrl+S` | Save file in the editor |
| `Ctrl+Shift+W` | Close tab |
| `Ctrl+PgUp` / `Ctrl+PgDn` | Switch tabs |
| `Ctrl+Shift+B` | Broadcast bar |

## Notes

- **Dashboard and tunnels** run their own SSH commands, so they need key-based login. For password servers, open the Terminal tab once and the dashboard reuses that connection (OpenSSH `ControlMaster`). Use **⋮ → copy SSH key** to switch a server to keys.
- Hop's own settings (groups, notes, snippets, tunnels) are in `~/.config/hop/`.

## Uninstall

```bash
./uninstall.sh          # keeps ~/.config/hop
./uninstall.sh --purge  # removes it too
```

---

## ภาษาไทย

**Hop** คือโปรแกรมจัดการ SSH บน Linux ที่ทำทุกอย่างจบในหน้าต่างเดียว

- 🗂️ การ์ดเซิร์ฟเวอร์ บอกสถานะออนไลน์/ออฟไลน์และความเร็ว มีโลโก้ OS ที่ตรวจให้เอง จัดกลุ่มและใส่โน้ตได้
- 🖥️ Terminal ในตัว เปิดเซิร์ฟเวอร์ไหนก็เป็นแท็บใหม่ในแอป สีตามธีม terminal ของเครื่อง
- 📁 จัดการไฟล์ผ่าน SFTP: อัปโหลด/ดาวน์โหลดได้ทั้งไฟล์และโฟลเดอร์ ลากไฟล์มาวางได้ เปลี่ยนชื่อ ลบ สร้างโฟลเดอร์ และมีแถบความคืบหน้า
- ✏️ แก้ไฟล์บนเซิร์ฟเวอร์ ดับเบิลคลิกไฟล์ข้อความเพื่อเปิด editor ที่มีสีโค้ด แล้วกด `Ctrl+S` เซฟกลับขึ้นเซิร์ฟเวอร์
- 📊 Dashboard: กราฟ CPU / RAM / Network แบบสด ดิสก์ uptime และ process ที่กินเครื่อง
- 🪟 Split view: terminal กับไฟล์อยู่คู่กัน เปิดโฟลเดอร์ไหน terminal จะ `cd` ตาม
- ⭐ Snippets: บันทึกคำสั่งที่ใช้บ่อย กดทีเดียวรัน
- 🚇 Port forwarding: เปิดเว็บหรือฐานข้อมูลบนเซิร์ฟเวอร์ผ่าน `localhost` ด้วยสวิตช์
- 📡 สั่งหลายเครื่องพร้อมกัน: พิมพ์ครั้งเดียว ส่งไปทุกเครื่องที่เลือก
- ⌨️ ใช้ใน terminal ก็ได้: พิมพ์ `hop`

เซิร์ฟเวอร์ทั้งหมดเก็บใน `~/.ssh/config` ตามปกติ ใช้ `ssh ชื่อ` ได้เหมือนเดิม

### ติดตั้ง

```bash
git clone https://github.com/arkam16666/hop-ssh.git
cd hop-ssh
./install.sh
```

ไม่ต้องใช้ sudo ติดตั้งลงในโฟลเดอร์ home ทั้งหมด

## License

MIT. See [LICENSE](LICENSE).
