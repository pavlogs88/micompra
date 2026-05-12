import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import json
import re
from PIL import Image
import io

# ── Configuración de página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="MiCompra",
    page_icon="🛒",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# ── CSS personalizado ────────────────────────────────────────────────────────
st.markdown("""
<style>
  .block-container { padding-top: 1rem; padding-bottom: 2rem; max-width: 480px; }
  .stButton > button {
    width: 100%; border-radius: 10px; font-weight: 500;
    background-color: #3ddc84; color: #000; border: none;
    padding: 0.6rem 1rem; font-size: 1rem;
  }
  .stButton > button:hover { background-color: #2ab56a; color: #000; }
  .total-box {
    background: #1a1a1a; border: 1px solid #3ddc84;
    border-radius: 12px; padding: 14px 18px;
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 1rem;
  }
  .total-label { color: #888; font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; }
  .total-amount { color: #3ddc84; font-size: 26px; font-weight: 600; font-family: monospace; }
  .item-row {
    background: #1a1a1a; border: 1px solid #2a2a2a;
    border-radius: 10px; padding: 10px 14px; margin-bottom: 6px;
  }
  .item-name { font-weight: 500; font-size: 14px; }
  .item-meta { color: #888; font-size: 12px; margin-top: 2px; }
  .item-price { color: #3ddc84; font-weight: 600; font-family: monospace; font-size: 15px; }
  .tag {
    display: inline-block; background: #2a2a2a; color: #888;
    border-radius: 20px; padding: 2px 8px; font-size: 11px; margin-right: 4px;
  }
  .stTabs [data-baseweb="tab"] { font-weight: 500; }
  div[data-testid="stNumberInput"] input { font-size: 16px; }
  div[data-testid="stTextInput"] input { font-size: 15px; }
</style>
""", unsafe_allow_html=True)

# ── Categorías por defecto ───────────────────────────────────────────────────
DEFAULT_CATS = {
    "Lácteos":           ["Leche", "Yogur", "Queso", "Manteca", "Crema", "Dulce de leche"],
    "Carnes":            ["Vacuno", "Pollo", "Cerdo", "Fiambres y embutidos", "Pescado"],
    "Verduras y Frutas": ["Verduras", "Frutas", "Legumbres secas"],
    "Panificados":       ["Pan", "Galletitas", "Cereales", "Pastas secas", "Arroz y harinas"],
    "Bebidas":           ["Agua", "Gaseosas", "Jugos", "Infusiones", "Vinos y cervezas"],
    "Limpieza":          ["Jabones", "Detergentes", "Desinfectantes", "Papel y descartables"],
    "Higiene personal":  ["Shampoo", "Cremas", "Higiene dental", "Desodorantes"],
    "Congelados":        ["Helados", "Pizzas y empanadas", "Verduras congeladas"],
    "Almacén":           ["Aceite y vinagre", "Conservas", "Condimentos", "Azúcar y dulces"],
    "Otros":             ["General", "Mascotas", "Bebé"],
}

# ── Estado de sesión ─────────────────────────────────────────────────────────
if "items"      not in st.session_state: st.session_state.items = []
if "categories" not in st.session_state: st.session_state.categories = DEFAULT_CATS.copy()
if "ai_data"    not in st.session_state: st.session_state.ai_data = {}
if "last_photo" not in st.session_state: st.session_state.last_photo = None

# ── Helpers ──────────────────────────────────────────────────────────────────
def fmt_price(n):
    return f"${n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def total():
    return sum(i["price"] * i["qty"] for i in st.session_state.items)

def get_sheet():
    """Conecta con Google Sheets usando el archivo de credenciales."""
    try:
        creds_data = json.loads(st.secrets["GOOGLE_CREDS"])
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet_id = st.secrets["SHEET_ID"]
        return gc.open_by_key(sheet_id).sheet1
    except Exception as e:
        st.error(f"Error conectando con Google Sheets: {e}")
        return None

def write_to_sheet(item):
    """Escribe una fila en el Google Sheet."""
    sheet = get_sheet()
    if not sheet:
        return False
    try:
        # Si la hoja está vacía, agrega encabezados
        if sheet.row_count == 0 or not sheet.row_values(1):
            sheet.append_row([
                "Fecha", "Hora", "Categoría", "Subcategoría",
                "Descripción", "Cantidad", "Unidad",
                "Precio Unitario", "Total"
            ])
        now = datetime.now()
        sheet.append_row([
            now.strftime("%d/%m/%Y"),
            now.strftime("%H:%M"),
            item["cat"],
            item["sub"],
            item["desc"],
            item["qty"],
            item["unit"],
            item["price"],
            round(item["price"] * item["qty"], 2)
        ])
        return True
    except Exception as e:
        st.error(f"Error escribiendo en Sheet: {e}")
        return False

def analyze_image_with_gemini(image_bytes):
    """Usa Gemini para extraer datos del producto desde la imagen."""
    try:
        gemini_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-1.5-flash")

        img = Image.open(io.BytesIO(image_bytes))

        cats_str = ", ".join(st.session_state.categories.keys())
        prompt = f"""Analizá esta imagen de una etiqueta o cartel de precio de supermercado argentino.
Extraé la información visible y respondé SOLO con un JSON válido, sin texto adicional ni markdown.

Formato exacto:
{{"descripcion": "nombre del producto", "precio": 0.00, "categoria": "una de: {cats_str}", "subcategoria": "subcategoría apropiada", "unidad": "unidad|kg|100g|L"}}

Si un campo no se puede determinar con certeza, usá cadena vacía o 0.
Respondé SOLO el JSON, nada más."""

        response = model.generate_content([prompt, img])
        text = response.text.strip()

        # Limpia posible markdown
        text = re.sub(r"```json|```", "", text).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        st.error(f"Error analizando imagen: {e}")
    return {}

# ── TABS ─────────────────────────────────────────────────────────────────────
tab_cargar, tab_lista, tab_config = st.tabs(["🛒 Cargar", "📋 Lista", "⚙️ Config"])

# ════════════════════════════════════════════════════════════
# TAB CARGAR
# ════════════════════════════════════════════════════════════
with tab_cargar:

    # Total siempre visible
    st.markdown(f"""
    <div class="total-box">
      <div>
        <div class="total-label">Total estimado</div>
        <div class="total-amount">{fmt_price(total())}</div>
      </div>
      <div style="color:#555;font-size:13px">{len(st.session_state.items)} productos</div>
    </div>
    """, unsafe_allow_html=True)

    # ── Foto con cámara ──────────────────────────────────────
    st.markdown("#### 📷 Fotografiar etiqueta")
    photo = st.camera_input("Apuntá al precio o etiqueta del producto", label_visibility="collapsed")

    if photo and photo != st.session_state.last_photo:
        st.session_state.last_photo = photo
        with st.spinner("Analizando con IA..."):
            data = analyze_image_with_gemini(photo.getvalue())
            if data:
                st.session_state.ai_data = data
                st.success("✓ IA completó los campos. Revisá y corregí si hace falta.")
            else:
                st.warning("No se pudo extraer info. Completá los campos a mano.")

    ai = st.session_state.ai_data

    # ── Formulario ───────────────────────────────────────────
    st.markdown("#### Datos del producto")

    cats = list(st.session_state.categories.keys())
    default_cat = ai.get("categoria", cats[0]) if ai.get("categoria") in cats else cats[0]
    cat = st.selectbox("Categoría", cats, index=cats.index(default_cat))

    subs = st.session_state.categories.get(cat, ["General"])
    ai_sub = ai.get("subcategoria", "")
    default_sub = ai_sub if ai_sub in subs else subs[0]
    sub = st.selectbox("Subcategoría", subs, index=subs.index(default_sub))

    desc = st.text_input("Descripción del producto", value=ai.get("descripcion", ""),
                         placeholder="Ej: Leche La Serenísima entera 1L")

    col1, col2 = st.columns(2)
    with col1:
        qty = st.number_input("Cantidad", min_value=1, value=1, step=1)
    with col2:
        price_val = float(ai.get("precio", 0.0)) if ai.get("precio") else 0.0
        price = st.number_input("Precio unitario ($)", min_value=0.0, value=price_val,
                                step=10.0, format="%.2f")

    units = ["unidad", "kg", "100g", "L"]
    ai_unit = ai.get("unidad", "unidad")
    unit_idx = units.index(ai_unit) if ai_unit in units else 0
    unit = st.radio("Unidad", units, index=unit_idx, horizontal=True)

    # ── Botón agregar ────────────────────────────────────────
    if st.button("+ Agregar al carrito", type="primary"):
        if not desc:
            st.error("Ingresá una descripción del producto.")
        elif price <= 0:
            st.error("Ingresá un precio válido.")
        else:
            item = {
                "id": int(datetime.now().timestamp() * 1000),
                "desc": desc, "price": price, "qty": qty,
                "unit": unit, "cat": cat, "sub": sub,
                "ts": datetime.now().isoformat()
            }
            st.session_state.items.append(item)
            st.session_state.ai_data = {}
            st.session_state.last_photo = None

            # Enviar a Google Sheet en background
            ok = write_to_sheet(item)
            subtotal = price * qty
            msg = f"✓ Agregado: {desc} — {fmt_price(subtotal)}"
            if ok:
                msg += " · Guardado en Sheets ✓"
            st.success(msg)
            st.rerun()

# ════════════════════════════════════════════════════════════
# TAB LISTA
# ════════════════════════════════════════════════════════════
with tab_lista:
    if not st.session_state.items:
        st.markdown("""
        <div style="text-align:center;padding:60px 0;color:#555">
          <div style="font-size:40px;margin-bottom:12px">🛒</div>
          <div>Todavía no cargaste productos.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div class="total-box">
          <div>
            <div class="total-label">Total de la compra</div>
            <div class="total-amount">{fmt_price(total())}</div>
          </div>
          <div style="color:#555;font-size:13px">{len(st.session_state.items)} productos</div>
        </div>
        """, unsafe_allow_html=True)

        for item in reversed(st.session_state.items):
            col_info, col_del = st.columns([5, 1])
            with col_info:
                st.markdown(f"""
                <div class="item-row">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div class="item-name">{item['desc']}</div>
                    <div class="item-price">{fmt_price(item['price'] * item['qty'])}</div>
                  </div>
                  <div class="item-meta">
                    <span class="tag">{item['cat']}</span>
                    <span class="tag">{item['sub']}</span>
                    <span class="tag">{item['qty']} {item['unit']}</span>
                    &nbsp;·&nbsp; {fmt_price(item['price'])} c/u
                  </div>
                </div>
                """, unsafe_allow_html=True)
            with col_del:
                if st.button("✕", key=f"del_{item['id']}"):
                    st.session_state.items = [i for i in st.session_state.items if i["id"] != item["id"]]
                    st.rerun()

        st.markdown("---")
        if st.button("🗑️ Limpiar todo", type="secondary"):
            st.session_state.items = []
            st.rerun()

# ════════════════════════════════════════════════════════════
# TAB CONFIG
# ════════════════════════════════════════════════════════════
with tab_config:
    st.markdown("### ⚙️ Configuración")

    with st.expander("📋 Instrucciones de configuración", expanded=False):
        st.markdown("""
**Para que la app funcione necesitás configurar 3 cosas en Streamlit Cloud:**

1. **Gemini API Key** (gratis) → `GEMINI_API_KEY`
2. **Google Sheets ID** → `SHEET_ID`
3. **Credenciales de Service Account** → `GOOGLE_CREDS`

Ver el archivo `INSTRUCCIONES.md` incluido para el paso a paso completo.
        """)

    st.markdown("### 🏷️ Categorías y subcategorías")

    for cat_name in list(st.session_state.categories.keys()):
        with st.expander(f"📁 {cat_name}"):
            subs = st.session_state.categories[cat_name]
            st.write("Subcategorías: " + ", ".join(subs))

            new_sub = st.text_input(f"Nueva subcategoría en {cat_name}",
                                    key=f"ns_{cat_name}", placeholder="Escribí y presioná Enter...")
            col_add, col_del = st.columns(2)
            with col_add:
                if st.button("+ Agregar subcategoría", key=f"as_{cat_name}"):
                    if new_sub and new_sub not in subs:
                        st.session_state.categories[cat_name].append(new_sub)
                        st.rerun()
            with col_del:
                if st.button("🗑️ Eliminar categoría", key=f"dc_{cat_name}"):
                    del st.session_state.categories[cat_name]
                    st.rerun()

    st.markdown("---")
    new_cat = st.text_input("Nueva categoría", placeholder="Nombre de la nueva categoría...")
    if st.button("+ Agregar categoría"):
        if new_cat and new_cat not in st.session_state.categories:
            st.session_state.categories[new_cat] = ["General"]
            st.rerun()
        elif new_cat:
            st.warning("Esa categoría ya existe.")
