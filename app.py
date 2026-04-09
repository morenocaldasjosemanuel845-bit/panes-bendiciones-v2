import os
import shutil
import sqlite3
from functools import wraps
from urllib.parse import quote
from uuid import uuid4

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    send_from_directory,
    abort,
)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esto-en-produccion")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "123456")

WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER", "51940849095")
STORE_NAME = os.environ.get("STORE_NAME", "Panes Artesanales Las 3 Bendiciones")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_DB_PATH = os.path.join(BASE_DIR, "tienda.db")
LEGACY_UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

DEFAULT_DATA_DIR = "/var/data" if os.path.isdir("/var/data") else os.path.join(BASE_DIR, "data")
DATA_DIR = os.environ.get("DATA_DIR", DEFAULT_DATA_DIR)

DB_PATH = os.path.join(DATA_DIR, "tienda.db")
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LEGACY_UPLOAD_FOLDER, exist_ok=True)

# Migra la base de datos antigua si aún existe y la nueva no
if not os.path.exists(DB_PATH) and os.path.exists(LEGACY_DB_PATH):
    shutil.copy2(LEGACY_DB_PATH, DB_PATH)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def column_exists(conn, table_name, column_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            posicion INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


def migrar_imagenes_antiguas():
    conn = get_db()

    if column_exists(conn, "productos", "imagen"):
        productos = conn.execute("""
            SELECT id, imagen
            FROM productos
            WHERE imagen IS NOT NULL AND TRIM(imagen) <> ''
        """).fetchall()

        for producto in productos:
            existe = conn.execute("""
                SELECT 1
                FROM product_images
                WHERE producto_id = ? AND filename = ?
            """, (producto["id"], producto["imagen"])).fetchone()

            if not existe:
                siguiente = conn.execute("""
                    SELECT COALESCE(MAX(posicion), -1) + 1 AS siguiente
                    FROM product_images
                    WHERE producto_id = ?
                """, (producto["id"],)).fetchone()["siguiente"]

                conn.execute("""
                    INSERT INTO product_images (producto_id, filename, posicion)
                    VALUES (?, ?, ?)
                """, (producto["id"], producto["imagen"], siguiente))

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


def eliminar_archivo_imagen(filename):
    for carpeta in [UPLOAD_FOLDER, LEGACY_UPLOAD_FOLDER]:
        ruta = os.path.join(carpeta, filename)
        if os.path.exists(ruta):
            os.remove(ruta)


def obtener_mapa_imagenes(conn, producto_ids):
    if not producto_ids:
        return {}

    placeholders = ",".join(["?"] * len(producto_ids))
    rows = conn.execute(
        f"""
        SELECT id, producto_id, filename, posicion
        FROM product_images
        WHERE producto_id IN ({placeholders})
        ORDER BY producto_id ASC, posicion ASC, id ASC
        """,
        producto_ids
    ).fetchall()

    mapa = {}
    for row in rows:
        mapa.setdefault(row["producto_id"], []).append({
            "id": row["id"],
            "producto_id": row["producto_id"],
            "filename": row["filename"],
            "posicion": row["posicion"],
        })

    return mapa


def enriquecer_productos(productos):
    conn = get_db()
    ids = [p["id"] for p in productos]
    mapa_imagenes = obtener_mapa_imagenes(conn, ids)
    conn.close()

    enriquecidos = []
    for p in productos:
        imagenes = mapa_imagenes.get(p["id"], [])
        imagen_principal = imagenes[0]["filename"] if imagenes else (p["imagen"] if "imagen" in p.keys() else None)

        enriquecidos.append({
            "id": p["id"],
            "nombre": p["nombre"],
            "descripcion": p["descripcion"],
            "precio": p["precio"],
            "imagenes": imagenes,
            "imagen_principal": imagen_principal,
            "cantidad_imagenes": len(imagenes),
        })

    return enriquecidos


def guardar_nuevas_imagenes(conn, producto_id, archivos):
    if not archivos:
        return

    siguiente = conn.execute("""
        SELECT COALESCE(MAX(posicion), -1) + 1 AS siguiente
        FROM product_images
        WHERE producto_id = ?
    """, (producto_id,)).fetchone()["siguiente"]

    for i, imagen in enumerate(archivos):
        extension = secure_filename(imagen.filename).rsplit(".", 1)[1].lower()
        nombre_imagen = f"{uuid4().hex}.{extension}"
        imagen.save(os.path.join(UPLOAD_FOLDER, nombre_imagen))

        conn.execute("""
            INSERT INTO product_images (producto_id, filename, posicion)
            VALUES (?, ?, ?)
        """, (producto_id, nombre_imagen, siguiente + i))


def reordenar_imagenes(conn, producto_id):
    imagenes = conn.execute("""
        SELECT id
        FROM product_images
        WHERE producto_id = ?
        ORDER BY posicion ASC, id ASC
    """, (producto_id,)).fetchall()

    for nuevo_indice, imagen in enumerate(imagenes):
        conn.execute("""
            UPDATE product_images
            SET posicion = ?
            WHERE id = ?
        """, (nuevo_indice, imagen["id"]))


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

    mapa_imagenes = obtener_mapa_imagenes(conn, ids)
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
        imagenes = mapa_imagenes.get(producto["id"], [])
        imagen_principal = imagenes[0]["filename"] if imagenes else (producto["imagen"] if "imagen" in producto.keys() else None)

        items.append({
            "id": producto["id"],
            "nombre": producto["nombre"],
            "precio": precio,
            "cantidad": cantidad,
            "subtotal": subtotal,
            "imagen_principal": imagen_principal,
        })

        total += subtotal
        cantidad_total += cantidad

    items.sort(key=lambda x: x["id"], reverse=True)
    return items, total, cantidad_total


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    ruta_nueva = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(ruta_nueva):
        return send_from_directory(UPLOAD_FOLDER, filename)

    ruta_antigua = os.path.join(LEGACY_UPLOAD_FOLDER, filename)
    if os.path.exists(ruta_antigua):
        return send_from_directory(LEGACY_UPLOAD_FOLDER, filename)

    abort(404)


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

    productos = enriquecer_productos(productos)
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
        lineas.append(f"- {item['nombre']} x{item['cantidad']} = S/ {item['subtotal']:.2f}")

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

    productos = enriquecer_productos(productos)
    return render_template("admin_panel.html", productos=productos)


@app.route("/admin/producto/nuevo", methods=["GET", "POST"])
@admin_required
def nuevo_producto():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        precio = request.form.get("precio", "").strip()
        imagenes = [img for img in request.files.getlist("imagenes") if img and img.filename]

        if not nombre or not precio:
            flash("El nombre y el precio son obligatorios.")
            return redirect(url_for("nuevo_producto"))

        try:
            precio = float(precio)
        except ValueError:
            flash("El precio debe ser un número válido.")
            return redirect(url_for("nuevo_producto"))

        if len(imagenes) < 1:
            flash("Debes subir al menos 1 imagen del producto.")
            return redirect(url_for("nuevo_producto"))

        if len(imagenes) > 5:
            flash("Solo puedes subir un máximo de 5 imágenes por producto.")
            return redirect(url_for("nuevo_producto"))

        for imagen in imagenes:
            if not allowed_file(imagen.filename):
                flash("Formato de imagen no permitido. Usa PNG, JPG, JPEG o WEBP.")
                return redirect(url_for("nuevo_producto"))

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO productos (nombre, descripcion, precio, imagen) VALUES (?, ?, ?, ?)",
            (nombre, descripcion, precio, None)
        )
        producto_id = cur.lastrowid

        guardar_nuevas_imagenes(conn, producto_id, imagenes)

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

    imagenes_actuales = conn.execute("""
        SELECT id, producto_id, filename, posicion
        FROM product_images
        WHERE producto_id = ?
        ORDER BY posicion ASC, id ASC
    """, (id,)).fetchall()

    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        precio = request.form.get("precio", "").strip()
        nuevas_imagenes = [img for img in request.files.getlist("imagenes") if img and img.filename]
        ids_eliminar = set(request.form.getlist("imagenes_eliminar"))

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

        for imagen in nuevas_imagenes:
            if not allowed_file(imagen.filename):
                conn.close()
                flash("Formato de imagen no permitido. Usa PNG, JPG, JPEG o WEBP.")
                return redirect(url_for("editar_producto", id=id))

        imagenes_a_eliminar = [img for img in imagenes_actuales if str(img["id"]) in ids_eliminar]

        total_final = (len(imagenes_actuales) - len(imagenes_a_eliminar)) + len(nuevas_imagenes)

        if total_final < 1:
            conn.close()
            flash("Cada producto debe tener al menos 1 imagen.")
            return redirect(url_for("editar_producto", id=id))

        if total_final > 5:
            conn.close()
            flash("Solo puedes tener un máximo de 5 imágenes por producto.")
            return redirect(url_for("editar_producto", id=id))

        conn.execute("""
            UPDATE productos
            SET nombre = ?, descripcion = ?, precio = ?
            WHERE id = ?
        """, (nombre, descripcion, precio, id))

        for img in imagenes_a_eliminar:
            conn.execute("DELETE FROM product_images WHERE id = ? AND producto_id = ?", (img["id"], id))
            eliminar_archivo_imagen(img["filename"])

        guardar_nuevas_imagenes(conn, id, nuevas_imagenes)
        reordenar_imagenes(conn, id)

        conn.commit()
        conn.close()

        flash("Producto actualizado correctamente.")
        return redirect(url_for("panel_admin"))

    conn.close()

    producto = {
        "id": producto["id"],
        "nombre": producto["nombre"],
        "descripcion": producto["descripcion"],
        "precio": producto["precio"],
        "imagenes": [
            {
                "id": img["id"],
                "filename": img["filename"],
                "posicion": img["posicion"],
            }
            for img in imagenes_actuales
        ]
    }

    return render_template("producto_form.html", producto=producto)


@app.route("/admin/producto/eliminar/<int:id>", methods=["POST"])
@admin_required
def eliminar_producto(id):
    conn = get_db()
    producto = conn.execute("SELECT * FROM productos WHERE id = ?", (id,)).fetchone()

    if producto:
        imagenes = conn.execute("""
            SELECT filename
            FROM product_images
            WHERE producto_id = ?
        """, (id,)).fetchall()

        for imagen in imagenes:
            eliminar_archivo_imagen(imagen["filename"])

        if producto["imagen"]:
            eliminar_archivo_imagen(producto["imagen"])

        conn.execute("DELETE FROM productos WHERE id = ?", (id,))
        conn.commit()

    conn.close()
    flash("Producto eliminado correctamente.")
    return redirect(url_for("panel_admin"))


if __name__ == "__main__":
    crear_tablas()
    migrar_imagenes_antiguas()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)