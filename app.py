"""
Sistema de Preventa - API REST
Distribuidora de Abarrotes
Conectada a PostgreSQL via DATABASE_URL
"""

import os
import copy
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from flask_cors import CORS
import psycopg2
import psycopg2.extras

load_dotenv()

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get("DATABASE_URL")

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get("DATABASE_URL")

# ─────────────────────────────────────────────
#  CONEXIÓN
# ─────────────────────────────────────────────

def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn

def query(sql, params=None, fetch="all"):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            conn.commit()
            if fetch == "all":
                return cur.fetchall()
            if fetch == "one":
                return cur.fetchone()
            return None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

carritos_db = {}   # en memoria, por sesión

def error(msg, code=400):
    return jsonify({"error": msg}), code

def ok(data, code=200):
    return jsonify(data), code

# ─────────────────────────────────────────────
#  PRODUCTOS
# ─────────────────────────────────────────────

@app.route("/productos", methods=["GET"])
def get_productos():
    categoria = request.args.get("categoria")
    if categoria:
        rows = query(
            "SELECT * FROM abarrotes_productos WHERE activo = TRUE AND categoria ILIKE %s ORDER BY id",
            (categoria,)
        )
    else:
        rows = query("SELECT * FROM abarrotes_productos WHERE activo = TRUE ORDER BY id")
    resultado = [dict(r) for r in rows]
    return ok({"productos": resultado, "total": len(resultado)})


@app.route("/productos/<int:pid>", methods=["GET"])
def get_producto(pid):
    row = query("SELECT * FROM abarrotes_productos WHERE id = %s AND activo = TRUE", (pid,), fetch="one")
    if not row:
        return error("Producto no encontrado", 404)
    return ok(dict(row))


@app.route("/productos", methods=["POST"])
def create_producto():
    data = request.get_json()
    if not data or not all(k in data for k in ("nombre", "precio", "categoria", "stock")):
        return error("Campos requeridos: nombre, precio, categoria, stock")
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO abarrotes_productos (nombre, categoria, precio) VALUES (%s, %s, %s) RETURNING *",
                (data["nombre"], data["categoria"], float(data["precio"]))
            )
            producto = dict(cur.fetchone())
            cur.execute(
                "INSERT INTO abarrotes_inventario (producto_id, stock) VALUES (%s, %s)",
                (producto["id"], int(data["stock"]))
            )
        conn.commit()
        return ok({"mensaje": "Producto creado", "producto": producto}, 201)
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()


@app.route("/productos/<int:pid>", methods=["PUT"])
def update_producto(pid):
    data = request.get_json() or {}
    campos = {k: data[k] for k in ("nombre", "precio", "categoria") if k in data}
    if not campos:
        return error("Nada que actualizar")
    set_clause = ", ".join(f"{k} = %s" for k in campos)
    values = list(campos.values()) + [pid]
    row = query(
        f"UPDATE abarrotes_productos SET {set_clause} WHERE id = %s AND activo = TRUE RETURNING *",
        values, fetch="one"
    )
    if not row:
        return error("Producto no encontrado", 404)
    return ok({"mensaje": "Producto actualizado", "producto": dict(row)})


@app.route("/productos/<int:pid>", methods=["DELETE"])
def delete_producto(pid):
    row = query(
        "UPDATE abarrotes_productos SET activo = FALSE WHERE id = %s AND activo = TRUE RETURNING id",
        (pid,), fetch="one"
    )
    if not row:
        return error("Producto no encontrado", 404)
    return ok({"mensaje": f"Producto {pid} eliminado"})

# ─────────────────────────────────────────────
#  CARRITO
# ─────────────────────────────────────────────

@app.route("/carrito", methods=["POST"])
def add_carrito():
    data = request.get_json()
    if not data or not all(k in data for k in ("cliente_id", "producto_id", "cantidad")):
        return error("Campos requeridos: cliente_id, producto_id, cantidad")
    cid, pid, cant = data["cliente_id"], data["producto_id"], int(data["cantidad"])
    if cant <= 0:
        return error("La cantidad debe ser mayor a 0")
    p = query("SELECT * FROM abarrotes_productos WHERE id = %s AND activo = TRUE", (pid,), fetch="one")
    if not p:
        return error("Producto no encontrado", 404)
    p = dict(p)
    inv = query("SELECT stock FROM abarrotes_inventario WHERE producto_id = %s", (pid,), fetch="one")
    if not inv or inv["stock"] < cant:
        return error(f"Stock insuficiente. Disponible: {inv['stock'] if inv else 0}")
    if cid not in carritos_db:
        carritos_db[cid] = []
    item = next((i for i in carritos_db[cid] if i["producto_id"] == pid), None)
    if item:
        item["cantidad"] += cant
        item["subtotal"]  = round(item["cantidad"] * float(p["precio"]), 2)
    else:
        carritos_db[cid].append({
            "producto_id": pid, "nombre": p["nombre"],
            "precio": float(p["precio"]), "cantidad": cant,
            "subtotal": round(cant * float(p["precio"]), 2),
        })
    total = round(sum(i["subtotal"] for i in carritos_db[cid]), 2)
    return ok({"mensaje": "Producto agregado al carrito", "carrito": carritos_db[cid], "total": total}, 201)


@app.route("/carrito", methods=["PUT"])
def update_carrito():
    data = request.get_json()
    if not data or not all(k in data for k in ("cliente_id", "producto_id", "cantidad")):
        return error("Campos requeridos: cliente_id, producto_id, cantidad")
    cid, pid, cant = data["cliente_id"], data["producto_id"], int(data["cantidad"])
    if cid not in carritos_db:
        return error("Carrito no encontrado", 404)
    item = next((i for i in carritos_db[cid] if i["producto_id"] == pid), None)
    if not item:
        return error("Producto no está en el carrito", 404)
    if cant <= 0:
        carritos_db[cid] = [i for i in carritos_db[cid] if i["producto_id"] != pid]
    else:
        inv = query("SELECT stock FROM abarrotes_inventario WHERE producto_id = %s", (pid,), fetch="one")
        if inv and inv["stock"] < cant:
            return error(f"Stock insuficiente. Disponible: {inv['stock']}")
        item["cantidad"] = cant
        item["subtotal"] = round(cant * item["precio"], 2)
    total = round(sum(i["subtotal"] for i in carritos_db[cid]), 2)
    return ok({"mensaje": "Carrito actualizado", "carrito": carritos_db[cid], "total": total})


@app.route("/carrito", methods=["DELETE"])
def delete_carrito():
    data = request.get_json()
    if not data or "cliente_id" not in data:
        return error("Campo requerido: cliente_id")
    cid = data["cliente_id"]
    if cid not in carritos_db:
        return error("Carrito no encontrado", 404)
    pid = data.get("producto_id")
    if pid:
        carritos_db[cid] = [i for i in carritos_db[cid] if i["producto_id"] != pid]
        return ok({"mensaje": f"Producto {pid} eliminado del carrito", "carrito": carritos_db[cid]})
    carritos_db[cid] = []
    return ok({"mensaje": "Carrito vaciado"})

# ─────────────────────────────────────────────
#  PEDIDOS
# ─────────────────────────────────────────────

@app.route("/pedidos", methods=["POST"])
def create_pedido():
    data = request.get_json()
    if not data or "cliente_id" not in data:
        return error("Campo requerido: cliente_id")
    cid = data["cliente_id"]
    if cid not in carritos_db or not carritos_db[cid]:
        return error("El carrito está vacío. Agregue productos antes de crear un pedido.", 400)
    items = carritos_db[cid]
    conn  = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for item in items:
                cur.execute(
                    "SELECT stock FROM abarrotes_inventario WHERE producto_id = %s FOR UPDATE",
                    (item["producto_id"],)
                )
                inv = cur.fetchone()
                if not inv or inv["stock"] < item["cantidad"]:
                    conn.rollback()
                    return error(f"Stock insuficiente para '{item['nombre']}'. Disponible: {inv['stock'] if inv else 0}")
            total = round(sum(i["subtotal"] for i in items), 2)
            cur.execute(
                "INSERT INTO abarrotes_pedidos (cliente_id, estado, total) VALUES (%s, 'pendiente', %s) RETURNING *",
                (cid, total)
            )
            pedido = dict(cur.fetchone())
            for item in items:
                cur.execute(
                    "INSERT INTO abarrotes_pedido_detalle (pedido_id, producto_id, cantidad, precio_unit) VALUES (%s, %s, %s, %s)",
                    (pedido["id"], item["producto_id"], item["cantidad"], item["precio"])
                )
                cur.execute(
                    "UPDATE abarrotes_inventario SET stock = stock - %s, actualizado_en = NOW() WHERE producto_id = %s",
                    (item["cantidad"], item["producto_id"])
                )
            cur.execute(
                "UPDATE abarrotes_pedidos SET estado = 'confirmado' WHERE id = %s RETURNING *",
                (pedido["id"],)
            )
            pedido = dict(cur.fetchone())
        conn.commit()
        carritos_db[cid] = []
        pedido["items"] = copy.deepcopy(items)
        return ok({"mensaje": "Pedido creado exitosamente", "pedido": pedido}, 201)
    except Exception as e:
        conn.rollback()
        return error(str(e))
    finally:
        conn.close()


@app.route("/pedidos", methods=["GET"])
def get_pedidos():
    rows = query("SELECT * FROM abarrotes_pedidos ORDER BY creado_en DESC")
    return ok({"pedidos": [dict(r) for r in rows], "total": len(rows)})

# ─────────────────────────────────────────────
#  INVENTARIO
# ─────────────────────────────────────────────

@app.route("/inventario", methods=["GET"])
def get_inventario():
    rows       = query("SELECT * FROM abarrotes_v_inventario ORDER BY id")
    inventario = [dict(r) for r in rows]
    bajo_stock = [i for i in inventario if i["bajo_stock"]]
    return ok({"inventario": inventario, "total_items": len(inventario), "alertas_bajo_stock": bajo_stock})

# ─────────────────────────────────────────────
#  INICIO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    if not DATABASE_URL:
        raise RuntimeError("Variable de entorno DATABASE_URL no definida")
    app.run(debug=True, port=5000)