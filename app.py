import os
import sqlite3
from functools import wraps
from urllib.parse import quote
from uuid import uuid4

from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esto-en-produccion")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "123456")

# Tu número en formato WhatsApp internacional para Perú
# 940849095 -> 51940849095
WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER", "51940849095")
STORE_NAME = os.environ.get("STORE_NAME", "Panes Artesanales Las 3 Bendiciones")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tienda.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def crear_tablas():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            precio REAL NOT NULL,
            imagen TEXT
        )
    """)

    conn.commit()
    conn.close()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin"):
            flash("Debes iniciar sesión como administrador.")
            return redirect(url_for("login_admin"))
        return f(*args, **kwargs)
    return decorated_function


def obtener_carrito():
    return session.get("carrito", {})


def guardar_carrito(carrito):
    session["carrito"] = carrito
    session.modified = True


def obtener_datos_carrito():
    carrito = obtener_carrito()

    if not carrito:
        return [], 0.0, 0

    ids = []
    for producto_id in carrito.keys():
        try:
            ids.append(int(producto_id))
        except ValueError:
            continue

    if not ids:
        return [], 0.0, 0

    conn = get_db()
    placeholders = ",".join(["?"] * len(ids))
    productos = conn.execute(
        f"SELECT * FROM productos WHERE id IN ({placeholders})",
        ids
    ).fetchall()
    conn.close()

    mapa_productos = {str(p["id"]): p for p in productos}

    items = []
    total = 0.0
    cantidad_total = 0

    for producto_id, cantidad in carrito.items():
        producto = mapa_productos.get(str(producto_id))
        if not producto:
            continue

        cantidad = int(cantidad)
        precio = float(producto["precio"])
        subtotal = precio * cantidad

        items.append({
            "id": producto["id"],
            "nombre": producto["nombre"],
            "precio": precio,
            "cantidad": cantidad,
            "subtotal": subtotal,
            "imagen": producto["imagen"],
        })

        total += subtotal
        cantidad_total += cantidad

    items.sort(key=lambda x: x["id"], reverse=True)
    return items, total, cantidad_total


@app.route("/")
@app.route("/tienda")
def tienda():
    busqueda = request.args.get("q", "").strip()

    conn = get_db()
    if busqueda:
        productos = conn.execute(
            """
            SELECT * FROM productos
            WHERE nombre LIKE ? OR descripcion LIKE ?
            ORDER BY id DESC
            """,
            (f"%{busqueda}%", f"%{busqueda}%")
        ).fetchall()
    else:
        productos = conn.execute("SELECT * FROM productos ORDER BY id DESC").fetchall()

    conn.close()

    carrito_items, total_carrito, cantidad_carrito = obtener_datos_carrito()

    return render_template(
        "tienda.html",
        productos=productos,
        busqueda=busqueda,
        carrito_items=carrito_items,
        total_carrito=total_carrito,
        cantidad_carrito=cantidad_carrito
    )


@app.route("/carrito/agregar/<int:id>", methods=["POST"])
def agregar_al_carrito(id):
    conn = get_db()
    producto = conn.execute("SELECT * FROM productos WHERE id = ?", (id,)).fetchone()
    conn.close()

    if not producto:
        flash("Producto no encontrado.")
        return redirect(url_for("tienda"))

    carrito = obtener_carrito()
    clave = str(id)
    carrito[clave] = int(carrito.get(clave, 0)) + 1
    guardar_carrito(carrito)

    flash(f"{producto['nombre']} fue agregado al carrito.")
    return redirect(request.referrer or url_for("tienda"))


@app.route("/carrito/quitar/<int:id>", methods=["POST"])
def quitar_del_carrito(id):
    carrito = obtener_carrito()
    clave = str(id)

    if clave in carrito:
        cantidad_actual = int(carrito[clave])

        if cantidad_actual > 1:
            carrito[clave] = cantidad_actual - 1
        else:
            carrito.pop(clave)

        guardar_carrito(carrito)
        flash("Producto actualizado en el carrito.")

    return redirect(request.referrer or url_for("tienda"))


@app.route("/carrito/vaciar", methods=["POST"])
def vaciar_carrito():
    session["carrito"] = {}
    session.modified = True
    flash("El carrito fue vaciado.")
    return redirect(request.referrer or url_for("tienda"))


@app.route("/whatsapp/comprar")
def comprar_por_whatsapp():
    carrito_items, total_carrito, cantidad_carrito = obtener_datos_carrito()

    if not carrito_items:
        flash("Tu carrito está vacío.")
        return redirect(url_for("tienda"))

    lineas = [
        f"Hola, quiero comprar en {STORE_NAME}:",
        ""
    ]

    for item in carrito_items:
        lineas.append(
            f"- {item['nombre']} x{item['cantidad']} = S/ {item['subtotal']:.2f}"
        )

    lineas.extend([
        "",
        f"Total de productos: {cantidad_carrito}",
        f"Total a pagar: S/ {total_carrito:.2f}",
        "",
        "Quedo atento(a) para confirmar el pedido y el método de pago."
    ])

    mensaje = "\n".join(lineas)
    url = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(mensaje)}"
    return redirect(url)


@app.route("/whatsapp/contacto")
def contacto_por_whatsapp():
    mensaje = (
        f"Hola, te comunicaste con la tienda {STORE_NAME}. "
        f"Quisiera más información sobre los productos."
    )
    url = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(mensaje)}"
    return redirect(url)


@app.route("/admin/login", methods=["GET", "POST"])
def login_admin():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        clave = request.form.get("clave", "").strip()

        if usuario == ADMIN_USER and clave == ADMIN_PASS:
            session["admin"] = True
            flash("Bienvenido al panel de administrador.")
            return redirect(url_for("panel_admin"))
        else:
            flash("Usuario o contraseña incorrectos.")

    return render_template("login_admin.html")


@app.route("/admin/logout", methods=["POST"])
def logout_admin():
    session.pop("admin", None)
    flash("Sesión cerrada correctamente.")
    return redirect(url_for("login_admin"))


@app.route("/admin")
@admin_required
def panel_admin():
    conn = get_db()
    productos = conn.execute("SELECT * FROM productos ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin_panel.html", productos=productos)


@app.route("/admin/producto/nuevo", methods=["GET", "POST"])
@admin_required
def nuevo_producto():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        precio = request.form.get("precio", "").strip()
        imagen = request.files.get("imagen")

        if not nombre or not precio:
            flash("El nombre y el precio son obligatorios.")
            return redirect(url_for("nuevo_producto"))

        try:
            precio = float(precio)
        except ValueError:
            flash("El precio debe ser un número válido.")
            return redirect(url_for("nuevo_producto"))

        nombre_imagen = None

        if imagen and imagen.filename:
            if not allowed_file(imagen.filename):
                flash("Formato de imagen no permitido. Usa PNG, JPG, JPEG o WEBP.")
                return redirect(url_for("nuevo_producto"))

            extension = secure_filename(imagen.filename).rsplit(".", 1)[1].lower()
            nombre_imagen = f"{uuid4().hex}.{extension}"
            imagen.save(os.path.join(UPLOAD_FOLDER, nombre_imagen))

        conn = get_db()
        conn.execute(
            "INSERT INTO productos (nombre, descripcion, precio, imagen) VALUES (?, ?, ?, ?)",
            (nombre, descripcion, precio, nombre_imagen)
        )
        conn.commit()
        conn.close()

        flash("Producto agregado correctamente.")
        return redirect(url_for("panel_admin"))

    return render_template("producto_form.html", producto=None)


@app.route("/admin/producto/editar/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_producto(id):
    conn = get_db()
    producto = conn.execute("SELECT * FROM productos WHERE id = ?", (id,)).fetchone()

    if not producto:
        conn.close()
        flash("Producto no encontrado.")
        return redirect(url_for("panel_admin"))

    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        precio = request.form.get("precio", "").strip()
        imagen = request.files.get("imagen")

        if not nombre or not precio:
            conn.close()
            flash("El nombre y el precio son obligatorios.")
            return redirect(url_for("editar_producto", id=id))

        try:
            precio = float(precio)
        except ValueError:
            conn.close()
            flash("El precio debe ser un número válido.")
            return redirect(url_for("editar_producto", id=id))

        nombre_imagen = producto["imagen"]

        if imagen and imagen.filename:
            if not allowed_file(imagen.filename):
                conn.close()
                flash("Formato de imagen no permitido. Usa PNG, JPG, JPEG o WEBP.")
                return redirect(url_for("editar_producto", id=id))

            extension = secure_filename(imagen.filename).rsplit(".", 1)[1].lower()
            nombre_imagen = f"{uuid4().hex}.{extension}"
            imagen.save(os.path.join(UPLOAD_FOLDER, nombre_imagen))

        conn.execute("""
            UPDATE productos
            SET nombre = ?, descripcion = ?, precio = ?, imagen = ?
            WHERE id = ?
        """, (nombre, descripcion, precio, nombre_imagen, id))
        conn.commit()
        conn.close()

        flash("Producto actualizado correctamente.")
        return redirect(url_for("panel_admin"))

    conn.close()
    return render_template("producto_form.html", producto=producto)


@app.route("/admin/producto/eliminar/<int:id>", methods=["POST"])
@admin_required
def eliminar_producto(id):
    conn = get_db()
    producto = conn.execute("SELECT * FROM productos WHERE id = ?", (id,)).fetchone()

    if producto:
        if producto["imagen"]:
            ruta_imagen = os.path.join(UPLOAD_FOLDER, producto["imagen"])
            if os.path.exists(ruta_imagen):
                os.remove(ruta_imagen)

        conn.execute("DELETE FROM productos WHERE id = ?", (id,))
        conn.commit()

    conn.close()
    flash("Producto eliminado correctamente.")
    return redirect(url_for("panel_admin"))


if __name__ == "__main__":
    crear_tablas()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)