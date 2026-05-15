# Mapeamento de categorias por Cod.Prod
# Fonte: base_temporaria fornecida por Eduardo
# Categorias: 0 = sem categoria / sem análise

CATEGORIAS_VALIDAS = [
    "CERVEJA",
    "CERVEJA ZERO",
    "CERVEJA MULTIPACK",
    "GIRO RGB",
    "HE",
    "HE RGB",
    "TRIMARCA RGB HE (Original)",
    "TRIMARCA RGB HE (Stella)",
    "TRIMARCA RGB HE (Spaten)",
    "NAB",
    "NAB ZERO",
    "MATCH",
    "LITRINHO",
    "MKTP",
    "BALANCED CHOICE",
]

# Mapeamento direto de categorias brutas (base_temporaria) → categorias do app
MAPA_CATEGORIA_BRUTA = {
    "NAB":                "NAB",
    "NAB ZERO":           "NAB ZERO",
    "CERVEJA RGB":        "GIRO RGB",
    "HE RGB":             "HE RGB",
    "HE":                 "HE",
    "HE BC":              "HE",
    "CERVEJA ZERO BC":    "CERVEJA ZERO",
    "CERVEJA MULTIPACK":  "CERVEJA MULTIPACK",
    "MKTPLACE":           "MKTP",
    "BALANCED CHOICE":    "BALANCED CHOICE",
    "BC":                 "BALANCED CHOICE",
    "MATCH":              "MATCH",
    "LITRINHO":           "LITRINHO",
    "0":                  None,
    "#N/D":               None,
    "":                   None,
}

# Marcas das trimarcas RGB HE (por Código Marca nos pedidos)
MARCAS_TRIMARCA = {
    "01090": "TRIMARCA RGB HE (Stella)",   # STELLA ARTOIS
    "05105": "TRIMARCA RGB HE (Spaten)",   # SPATEN N
    "00308": "TRIMARCA RGB HE (Original)", # ORIGINAL
}

# Códigos de produto das trimarcas RGB HE
PRODS_HE_RGB = {
    20530: "TRIMARCA RGB HE (Stella)",   # STELLA ARTOIS 600ML
    23186: "TRIMARCA RGB HE (Spaten)",   # SPATEN N 600ML
    2546:  "TRIMARCA RGB HE (Original)", # ORIGINAL 600ML
    20217: "TRIMARCA RGB HE (Original)", # ORIGINAL GFA VD 300ML
    29253: "TRIMARCA RGB HE (Original)", # ORIGINAL GFA VD 1L
    20535: "TRIMARCA RGB HE (Stella)",   # STELLA ARTOIS OW 600ML
    21668: "TRIMARCA RGB HE (Spaten)",   # SPATEN OW 600ML
}

def resolver_categoria(cod_prod, cat_bruta, desc_produto, cod_marca_str):
    """
    Resolve a categoria final de um produto.
    Prioridade:
    1. Se é trimarca RGB HE (por código de produto)
    2. Mapeamento da categoria bruta
    3. Litrinho por nome (GFA VD 300ML)
    4. Cerveja por nome sem categoria
    5. None (sem categoria)
    """
    try:
        cod_prod_int = int(str(cod_prod).strip())
    except:
        cod_prod_int = 0

    desc = str(desc_produto).upper().strip()
    cat = str(cat_bruta).strip()

    # 1. Trimarca por código de produto
    if cod_prod_int in PRODS_HE_RGB:
        return PRODS_HE_RGB[cod_prod_int]

    # 2. Trimarca por código de marca nos pedidos
    cod_marca = str(cod_marca_str).strip().lstrip("0") if cod_marca_str else ""
    for cod, cat_tri in MARCAS_TRIMARCA.items():
        if cod.lstrip("0") == cod_marca and "600" in desc:
            return cat_tri

    # 3. Cerveja sem embalagem RGB (lata, long neck etc) - LITRINHO só via produtos_base
    if cat == "CERVEJA":
        return "CERVEJA"

    # 5. Mapeamento direto da categoria bruta
    return MAPA_CATEGORIA_BRUTA.get(cat, None)


def carregar_base_produtos(df_base):
    """
    Carrega a base de produtos do Sheets (aba produtos_base)
    e retorna dict {cod_prod: categoria_app}
    """
    mapa = {}
    for _, row in df_base.iterrows():
        cod = str(row.get("cod", "")).strip()
        cat = str(row.get("categoria", "")).strip()
        if cod and cat and cat not in ("0", "#N/D", ""):
            cat_resolvida = MAPA_CATEGORIA_BRUTA.get(cat, None)
            if cat_resolvida:
                mapa[cod] = cat_resolvida
            elif cat in CATEGORIAS_VALIDAS:
                mapa[cod] = cat
    return mapa
