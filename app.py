# -*- coding: utf-8 -*-
"""
Your Home — CRM de Análises de Crédito
Flask + SQLite. Login por CPF, perfis Analista / Supervisor / Gerente / Diretor.
Execute:  python app.py   (ou iniciar.bat no Windows)
"""
import os, re, json, csv, io, sqlite3, secrets, functools, shutil, tempfile
from datetime import datetime, date, timedelta

from flask import (Flask, g, render_template, request, redirect, url_for, session,
                   flash, abort, Response, send_file)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("MBA_DB", os.path.join(BASE, "mba_crm.db"))
VERSAO = "1.3"
SENHA_PADRAO = "abc123"
MAX_TENTATIVAS = 5
BLOQUEIO_MIN = 15

PERFIS = {"analista": "Analista", "supervisor": "Supervisor", "gerente": "Gerente", "diretor": "Diretor"}
NIVEL = {"analista": 1, "supervisor": 2, "gerente": 3, "diretor": 4}
# quem pode criar/editar quem
GERENCIA = {"supervisor": ["analista"], "gerente": ["analista", "supervisor"],
            "diretor": ["analista", "supervisor", "gerente", "diretor"]}
# quem pode EDITAR os dados cadastrais (nome, CPF, e-mail) de quem — além do que já gerencia
EDITA_EXTRA = {"gerente": ["diretor"]}

STATUS = ["Em análise", "Pendente documentação", "Aprovado", "Aprovado - Condicional", "Reprovado"]
STATUS_ABERTOS = ["Em análise", "Pendente documentação"]
STATUS_APROVADOS = ["Aprovado", "Aprovado - Condicional"]
STATUS_INFO = {
    "Aprovado": ("aprovado", "CRÉDITO APROVADO"),
    "Aprovado - Condicional": ("aprovado", "CRÉDITO APROVADO - CONDICIONAL"),
    "Em análise": ("analise", "CRÉDITO EM ANÁLISE"),
    "Pendente documentação": ("pendente", "PENDENTE DE DOCUMENTAÇÃO"),
    "Reprovado": ("reprovado", "CRÉDITO REPROVADO"),
}
COMPLIANCE = ["Pendente", "Nada consta", "Consta", "Off"]
COMP_CLASS = {"Nada consta": "ok", "Consta": "bad", "Pendente": "warn", "Off": "off"}
MOTIVOS_REPROVA = ["Compromissos financeiros", "Dívidas vencidas", "Pendência interna", "Reprovado simulação"]

# ---- SLA dos protocolos (Anapro) ----
# Prazo total em minutos, contado a partir do horário de solicitação
SLA_MIN = {"LEAD": 60, "MESA": 30}
# Faltando esses minutos (ou menos), o protocolo fica AMARELO (prestes a atrasar)
SLA_AVISO_MIN = {"LEAD": 15, "MESA": 10}
# O prazo termina quando o analista clica em FINALIZAR
SLA_ESTADOS = {
    "no_prazo": ("azul", "No prazo"),
    "alerta": ("amarelo", "Prestes a atrasar"),
    "atrasado": ("vermelho", "Atrasado"),
    "cumprido": ("azul", "Concluído no prazo"),
    "atrasado_fim": ("vermelho", "Concluído com atraso"),
}

app = Flask(__name__)


def _secret_key():
    if os.environ.get("MBA_SECRET_KEY"):
        return os.environ["MBA_SECRET_KEY"]
    path = os.path.join(BASE, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app.config.update(
    SECRET_KEY=_secret_key(),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=9),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# ------------------------------------------------------------------ banco


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d is not None:
        d.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cpf TEXT UNIQUE NOT NULL,
  nome TEXT NOT NULL,
  email TEXT DEFAULT '',
  perfil TEXT NOT NULL,
  ativo INTEGER NOT NULL DEFAULT 1,
  senha_hash TEXT NOT NULL,
  trocar_senha INTEGER NOT NULL DEFAULT 1,
  tentativas INTEGER NOT NULL DEFAULT 0,
  bloqueado_ate TEXT,
  criado_em TEXT NOT NULL,
  ultimo_acesso TEXT
);
CREATE TABLE IF NOT EXISTS analises(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analista_id INTEGER NOT NULL REFERENCES usuarios(id),
  status TEXT NOT NULL,
  emissao TEXT NOT NULL,
  empreendimento TEXT DEFAULT '',
  construtora TEXT DEFAULT '',
  compradores TEXT DEFAULT '',
  cpfs TEXT DEFAULT '',
  financiamento REAL DEFAULT 0,
  total_aprovado REAL DEFAULT 0,
  renda_familiar REAL DEFAULT 0,
  vencimento_sicaq TEXT DEFAULT '',
  dados TEXT NOT NULL,
  criado_em TEXT NOT NULL,
  atualizado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_analises_analista ON analises(analista_id);
CREATE INDEX IF NOT EXISTS ix_analises_emissao ON analises(emissao);
CREATE TABLE IF NOT EXISTS historico(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analise_id INTEGER NOT NULL REFERENCES analises(id) ON DELETE CASCADE,
  usuario_id INTEGER REFERENCES usuarios(id),
  acao TEXT NOT NULL,
  em TEXT NOT NULL
);
"""


NOVAS_COLUNAS = [("protocolo", "TEXT DEFAULT ''"), ("tipo", "TEXT DEFAULT 'LEAD'"), ("solicitado_em", "TEXT"),
                  ("distribuido_por", "INTEGER"), ("distribuido_em", "TEXT"), ("concluido_em", "TEXT"),
                  ("iniciado_em", "TEXT"), ("motivo_reprova", "TEXT DEFAULT ''"), ("origem_id", "INTEGER")]


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    # migração automática de bancos da versão 1.0
    cols = {r[1] for r in con.execute("PRAGMA table_info(analises)")}
    for nome, tipo in NOVAS_COLUNAS:
        if nome not in cols:
            con.execute(f"ALTER TABLE analises ADD COLUMN {nome} {tipo}")
    con.execute("CREATE INDEX IF NOT EXISTS ix_analises_protocolo ON analises(protocolo)")
    con.commit()
    con.close()


def agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ utilidades


def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def cpf_valido(cpf):
    c = so_digitos(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(c[i]) * ((n + 1) - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != int(c[n]):
            return False
    return True


def fmt_cpf(s):
    c = so_digitos(s)
    if len(c) != 11:
        return s or ""
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"


def money(v):
    """Aceita 251.955,56 | 251955,56 | 251955.56 | 400.000 | R$ 1.000,00"""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v).replace("R$", "").replace(" ", "").replace("\xa0", "").strip()
    if not s:
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def brl(v, simbolo=True):
    txt = f"{(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("R$ " + txt) if simbolo else txt


def data_br(s):
    if not s:
        return "—"
    s = str(s)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return s


def data_hora_br(s):
    if not s:
        return "—"
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return s


def parse_data(s, padrao=None):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return padrao


def parse_dt(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except (TypeError, ValueError):
            pass
    return None


def fmt_min(m):
    if m is None:
        return "—"
    m = int(round(m))
    neg = m < 0
    m = abs(m)
    txt = f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"
    return ("-" if neg else "") + txt


def hora(s):
    d = parse_dt(s)
    return d.strftime("%d/%m %H:%M") if d else "—"


def sla(r, ref=None):
    """Situação do prazo de um protocolo. Retorna None se não tem horário de solicitação."""
    ini = parse_dt(r["solicitado_em"])
    if not ini:
        return None
    tipo = r["tipo"] if r["tipo"] in SLA_MIN else "LEAD"
    prazo = SLA_MIN[tipo]
    fim = parse_dt(r["concluido_em"])
    ref = fim or ref or datetime.now()
    decorrido = (ref - ini).total_seconds()
    restante = prazo * 60 - decorrido
    if fim:
        estado = "cumprido" if restante >= 0 else "atrasado_fim"
    elif restante < 0:
        estado = "atrasado"
    elif restante <= SLA_AVISO_MIN[tipo] * 60:
        estado = "alerta"
    else:
        estado = "no_prazo"
    cor, rotulo = SLA_ESTADOS[estado]
    return dict(estado=estado, cor=cor, rotulo=rotulo, tipo=tipo, prazo_min=prazo,
                decorrido_min=decorrido / 60, restante_seg=int(restante), aviso_seg=SLA_AVISO_MIN[tipo] * 60,
                prazo_em=(ini + timedelta(minutes=prazo)).strftime("%Y-%m-%d %H:%M:%S"), concluido=bool(fim),
                no_prazo=restante >= 0)


def tempo_analise(r):
    """Tempo entre INICIAR e FINALIZAR (ou até agora, se ainda em andamento)."""
    ini = parse_dt(r["iniciado_em"])
    if not ini:
        return None
    fim = parse_dt(r["concluido_em"])
    seg = ((fim or datetime.now()) - ini).total_seconds()
    return dict(minutos=seg / 60, segundos=int(seg), em_andamento=not fim)


app.jinja_env.filters.update(brl=brl, data=data_br, datahora=data_hora_br, cpf=fmt_cpf,
                             brlnum=lambda v: brl(v, False), minutos=fmt_min, hora=hora)
app.jinja_env.globals.update(sla=sla, tempo_analise=tempo_analise)


# ------------------------------------------------------------------ segurança


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.context_processor
def _ctx():
    return dict(csrf_token=csrf_token, usuario=g.get("user"), PERFIS=PERFIS, NIVEL=NIVEL,
                STATUS=STATUS, STATUS_INFO=STATUS_INFO, COMPLIANCE=COMPLIANCE, COMP_CLASS=COMP_CLASS,
                logo_existe=os.path.exists(os.path.join(BASE, "static", "logo.png")),
                hoje=date.today().isoformat(), VERSAO=VERSAO, SLA_MIN=SLA_MIN, SLA_AVISO_MIN=SLA_AVISO_MIN,
                MOTIVOS_REPROVA=MOTIVOS_REPROVA)


LIVRES = {"login", "static", "setup", "logout"}


@app.before_request
def _antes():
    if request.method == "POST":
        tok = request.form.get("_csrf")
        if not tok or tok != session.get("_csrf"):
            abort(400, "Sessão expirada. Recarregue a página e tente novamente.")

    g.user = None
    uid = session.get("uid")
    if uid:
        u = db().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
        if u and u["ativo"]:
            g.user = u
        else:
            session.clear()

    ep = request.endpoint or ""
    if ep in LIVRES:
        return
    if not g.user:
        if db().execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
            return redirect(url_for("setup"))
        return redirect(url_for("login", next=request.path))
    if g.user["trocar_senha"] and ep != "trocar_senha":
        return redirect(url_for("trocar_senha"))


def exige(nivel_min):
    def deco(fn):
        @functools.wraps(fn)
        def w(*a, **k):
            if NIVEL[g.user["perfil"]] < NIVEL[nivel_min]:
                abort(403)
            return fn(*a, **k)
        return w
    return deco


def e_gestor():
    return NIVEL[g.user["perfil"]] >= NIVEL["supervisor"]


@app.errorhandler(403)
def _403(e):
    return render_template("erro.html", titulo="Acesso negado",
                           msg="Seu perfil não tem permissão para acessar esta página."), 403


@app.errorhandler(404)
def _404(e):
    return render_template("erro.html", titulo="Não encontrado", msg="A página ou registro não existe."), 404


@app.errorhandler(400)
def _400(e):
    return render_template("erro.html", titulo="Requisição inválida", msg=e.description), 400


# ------------------------------------------------------------------ login


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if db().execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] > 0:
        return redirect(url_for("login"))
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cpf = so_digitos(request.form.get("cpf"))
        if not nome or not cpf_valido(cpf):
            flash("Informe o nome e um CPF válido.", "erro")
        else:
            db().execute("INSERT INTO usuarios(cpf,nome,email,perfil,senha_hash,trocar_senha,criado_em)"
                         " VALUES(?,?,?,?,?,1,?)",
                         (cpf, nome, request.form.get("email", "").strip(), "diretor",
                          generate_password_hash(SENHA_PADRAO), agora()))
            db().commit()
            flash(f"Diretor criado. Entre com o CPF e a senha padrão {SENHA_PADRAO}.", "ok")
            return redirect(url_for("login"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("inicio"))
    if db().execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
        return redirect(url_for("setup"))
    if request.method == "POST":
        cpf = so_digitos(request.form.get("cpf"))
        senha = request.form.get("senha", "")
        u = db().execute("SELECT * FROM usuarios WHERE cpf=?", (cpf,)).fetchone()
        erro = "CPF ou senha incorretos."
        if u and u["bloqueado_ate"] and u["bloqueado_ate"] > agora():
            erro = f"Acesso bloqueado por excesso de tentativas. Tente novamente após {data_hora_br(u['bloqueado_ate'])[-5:]}."
        elif u and not u["ativo"]:
            erro = "Usuário desativado. Procure seu gestor."
        elif u and check_password_hash(u["senha_hash"], senha):
            db().execute("UPDATE usuarios SET tentativas=0,bloqueado_ate=NULL,ultimo_acesso=? WHERE id=?",
                         (agora(), u["id"]))
            db().commit()
            session.clear()
            session.permanent = True
            session["uid"] = u["id"]
            if u["trocar_senha"]:
                return redirect(url_for("trocar_senha"))
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("inicio"))
        elif u:
            t = u["tentativas"] + 1
            bloq = None
            if t >= MAX_TENTATIVAS:
                bloq = (datetime.now() + timedelta(minutes=BLOQUEIO_MIN)).strftime("%Y-%m-%d %H:%M:%S")
                t = 0
                erro = f"Senha incorreta {MAX_TENTATIVAS} vezes. Acesso bloqueado por {BLOQUEIO_MIN} minutos."
            db().execute("UPDATE usuarios SET tentativas=?, bloqueado_ate=? WHERE id=?", (t, bloq, u["id"]))
            db().commit()
        flash(erro, "erro")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def senha_problema(nova, confirma, cpf):
    if nova != confirma:
        return "A confirmação não confere com a nova senha."
    if len(nova) < 8:
        return "A senha precisa ter pelo menos 8 caracteres."
    if not re.search(r"[A-Za-z]", nova) or not re.search(r"\d", nova):
        return "A senha precisa ter letras e números."
    if nova == SENHA_PADRAO or so_digitos(nova) == cpf:
        return "Escolha uma senha diferente da padrão e do seu CPF."
    return None


@app.route("/trocar-senha", methods=["GET", "POST"])
def trocar_senha():
    obrigatoria = bool(g.user["trocar_senha"])
    if request.method == "POST":
        atual = request.form.get("atual", "")
        nova = request.form.get("nova", "")
        prob = None
        if not check_password_hash(g.user["senha_hash"], atual):
            prob = "Senha atual incorreta."
        else:
            prob = senha_problema(nova, request.form.get("confirma", ""), g.user["cpf"])
            if not prob and check_password_hash(g.user["senha_hash"], nova):
                prob = "A nova senha precisa ser diferente da atual."
        if prob:
            flash(prob, "erro")
        else:
            db().execute("UPDATE usuarios SET senha_hash=?, trocar_senha=0 WHERE id=?",
                         (generate_password_hash(nova), g.user["id"]))
            db().commit()
            flash("Senha alterada com sucesso.", "ok")
            return redirect(url_for("inicio"))
    return render_template("trocar_senha.html", obrigatoria=obrigatoria)


@app.route("/")
def inicio():
    if g.user["perfil"] == "diretor":
        return redirect(url_for("relatorios"))
    if g.user["perfil"] == "gerente":
        return redirect(url_for("painel"))
    return redirect(url_for("fila"))


# ------------------------------------------------------------------ análises

CAMPOS_IMOVEL = ["construtora", "empreendimento", "torre", "uh", "inclusao", "corretor", "gerente",
                 "superintendente", "diretor"]
CAMPOS_FIN_MONEY = ["financiamento", "fgts", "subsidio", "proprios", "parcela"]


def analise_vazia():
    return {
        "imovel": {k: "" for k in CAMPOS_IMOVEL} | {"avaliacao": 0.0, "inclusao": date.today().isoformat()},
        "fin": {k: 0.0 for k in CAMPOS_FIN_MONEY} | {"vencimento": "", "prazo": 420, "amortizacao": "PRICE",
                                                     "enquadramento": "MCMV", "taxa": "", "meses36": "Sim",
                                                     "dependente": "Não"},
        "compradores": [{"nome": "", "cpf": "", "nascimento": "", "formal": 0.0, "informal": 0.0}],
        "faixa": "",
        "compliance": {"serasa": "Pendente", "cadmut": "Pendente", "ciweb": "Pendente", "subsidio": "Pendente"},
        "obs": "",
    }


def dados_do_form(f):
    d = analise_vazia()
    for k in CAMPOS_IMOVEL:
        d["imovel"][k] = f.get("imovel_" + k, "").strip()
    d["imovel"]["avaliacao"] = money(f.get("imovel_avaliacao"))
    for k in CAMPOS_FIN_MONEY:
        d["fin"][k] = money(f.get("fin_" + k))
    for k in ["vencimento", "amortizacao", "enquadramento", "taxa", "meses36", "dependente"]:
        d["fin"][k] = f.get("fin_" + k, "").strip()
    try:
        d["fin"]["prazo"] = int(so_digitos(f.get("fin_prazo")) or 0)
    except ValueError:
        d["fin"]["prazo"] = 0
    nomes, cpfs = f.getlist("c_nome"), f.getlist("c_cpf")
    nasc, formal, informal = f.getlist("c_nasc"), f.getlist("c_formal"), f.getlist("c_informal")
    comps = []
    for i in range(len(nomes)):
        c = {"nome": nomes[i].strip(), "cpf": so_digitos(cpfs[i] if i < len(cpfs) else ""),
             "nascimento": nasc[i] if i < len(nasc) else "",
             "formal": money(formal[i] if i < len(formal) else 0),
             "informal": money(informal[i] if i < len(informal) else 0)}
        if c["nome"] or c["cpf"] or c["formal"] or c["informal"]:
            comps.append(c)
    d["compradores"] = comps or analise_vazia()["compradores"]
    d["faixa"] = f.get("faixa", "").strip()
    for k in d["compliance"]:
        v = f.get("comp_" + k, "Pendente")
        d["compliance"][k] = v if v in COMPLIANCE else "Pendente"
    d["obs"] = f.get("obs", "").strip()
    return d


def calc(d):
    for c in d["compradores"]:
        c["renda"] = round(money(c.get("formal")) + money(c.get("informal")), 2)
    d["renda_familiar"] = round(sum(c["renda"] for c in d["compradores"]), 2)
    f = d["fin"]
    d["total_aprovado"] = round(sum(money(f.get(k)) for k in ["financiamento", "fgts", "subsidio", "proprios"]), 2)
    return d


SQL_BASE = ("SELECT a.*, u.nome AS analista_nome, u.perfil AS analista_perfil, d.nome AS distribuidor_nome"
            " FROM analises a JOIN usuarios u ON u.id=a.analista_id LEFT JOIN usuarios d ON d.id=a.distribuido_por")


def carregar(aid):
    a = db().execute(SQL_BASE + " WHERE a.id=?", (aid,)).fetchone()
    if not a:
        abort(404)
    if not e_gestor() and a["analista_id"] != g.user["id"]:
        abort(403)
    return a


def gravar(aid, analista_id, status, emissao, d):
    d = calc(d)
    campos = dict(
        analista_id=analista_id, status=status, emissao=emissao,
        empreendimento=d["imovel"]["empreendimento"], construtora=d["imovel"]["construtora"],
        compradores=" | ".join(c["nome"] for c in d["compradores"] if c["nome"]),
        cpfs=" ".join(c["cpf"] for c in d["compradores"] if c["cpf"]),
        financiamento=money(d["fin"]["financiamento"]), total_aprovado=d["total_aprovado"],
        renda_familiar=d["renda_familiar"], vencimento_sicaq=d["fin"]["vencimento"],
        dados=json.dumps(d, ensure_ascii=False), atualizado_em=agora())
    if aid:
        sets = ",".join(f"{k}=?" for k in campos)
        db().execute(f"UPDATE analises SET {sets} WHERE id=?", (*campos.values(), aid))
    else:
        campos["criado_em"] = campos["atualizado_em"]
        cols = ",".join(campos)
        cur = db().execute(f"INSERT INTO analises({cols}) VALUES({','.join('?' * len(campos))})",
                           tuple(campos.values()))
        aid = cur.lastrowid
    return aid


def log(aid, acao):
    db().execute("INSERT INTO historico(analise_id,usuario_id,acao,em) VALUES(?,?,?,?)",
                 (aid, g.user["id"], acao, agora()))


def analistas_ativos(incluir_id=None):
    return db().execute("SELECT id,nome,perfil,ativo FROM usuarios WHERE ativo=1 OR id=? ORDER BY nome",
                        (incluir_id or 0,)).fetchall()


def nome_usuario(uid):
    r = db().execute("SELECT nome FROM usuarios WHERE id=?", (uid,)).fetchone()
    return r["nome"] if r else "—"


def finalizar_analise(aid):
    """FINALIZAR: para o relógio do SLA e o tempo de análise."""
    cur = db().execute("UPDATE analises SET concluido_em=? WHERE id=? AND concluido_em IS NULL", (agora(), aid))
    if not cur.rowcount:
        return
    r = db().execute("SELECT * FROM analises WHERE id=?", (aid,)).fetchone()
    partes = [f"Análise finalizada por {g.user['nome']} — parecer: {r['status']}"
              + (f" ({r['motivo_reprova']})" if r["motivo_reprova"] else "")]
    t = tempo_analise(r)
    if t:
        partes.append(f"tempo de análise {fmt_min(t['minutos'])}")
    s = sla(r)
    if s:
        partes.append(f"SLA {fmt_min(s['decorrido_min'])} — {s['rotulo'].lower()} (prazo {s['tipo']}: {s['prazo_min']} min)")
    log(aid, " · ".join(partes))


def em_andamento_do(uid, exceto=None):
    return db().execute("SELECT * FROM analises WHERE analista_id=? AND iniciado_em IS NOT NULL AND concluido_em IS NULL"
                        " AND id<>? ORDER BY iniciado_em", (uid, exceto or 0)).fetchall()


def transferir_para(a, novo_id):
    if int(novo_id) == a["analista_id"]:
        return False
    reinicia = a["iniciado_em"] and not a["concluido_em"]
    db().execute("UPDATE analises SET analista_id=?, atualizado_em=?" + (", iniciado_em=NULL" if reinicia else "")
                 + " WHERE id=?", (novo_id, agora(), a["id"]))
    log(a["id"], f"Transferido de {a['analista_nome']} para {nome_usuario(novo_id)} por {g.user['nome']}"
        + (" — o novo analista precisa clicar em Iniciar" if reinicia else ""))
    return True


@app.route("/analises")
def analises():
    q = request.args.get("q", "").strip()
    st = request.args.get("status", "")
    an = request.args.get("analista", "")
    de, ate = request.args.get("de", ""), request.args.get("ate", "")
    sql = SQL_BASE + " WHERE 1=1"
    p = []
    if not e_gestor():
        sql += " AND a.analista_id=?"
        p.append(g.user["id"])
    elif an:
        sql += " AND a.analista_id=?"
        p.append(an)
    if st in STATUS:
        sql += " AND a.status=?"
        p.append(st)
    if parse_data(de):
        sql += " AND a.emissao>=?"
        p.append(de)
    if parse_data(ate):
        sql += " AND a.emissao<=?"
        p.append(ate)
    if q:
        like = f"%{q}%"
        sql += " AND (a.compradores LIKE ? OR a.empreendimento LIKE ? OR a.construtora LIKE ? OR a.protocolo LIKE ?"
        p += [like, like, like, like]
        dig = so_digitos(q)
        if dig:
            sql += " OR a.cpfs LIKE ?"
            p.append(f"%{dig}%")
        sql += ")"
    sql += " ORDER BY a.atualizado_em DESC LIMIT 500"
    rows = db().execute(sql, p).fetchall()
    return render_template("analises.html", rows=rows, f=request.args,
                           analistas=analistas_ativos() if e_gestor() else [])


@app.route("/analises/nova", methods=["GET", "POST"])
@exige("supervisor")
def analise_nova():
    if request.method == "POST":
        return salvar(None)
    return render_template("analise_form.html", a=None, d=calc(analise_vazia()), status="Em análise",
                           emissao=date.today().isoformat(), analista_id=g.user["id"], motivo="",
                           analistas=analistas_ativos())


@app.route("/analises/<int:aid>")
def analise_ver(aid):
    a = carregar(aid)
    d = calc(json.loads(a["dados"]))
    hist = db().execute("SELECT h.*, u.nome FROM historico h LEFT JOIN usuarios u ON u.id=h.usuario_id"
                        " WHERE h.analise_id=? ORDER BY h.id DESC LIMIT 30", (aid,)).fetchall()
    return render_template("relatorio.html", a=a, d=d, hist=hist,
                           transferiveis=analistas_para_fila() if e_gestor() else [])


@app.route("/analises/<int:aid>/editar", methods=["GET", "POST"])
def analise_editar(aid):
    a = carregar(aid)
    if precisa_iniciar(a):
        flash(f"Clique em Iniciar no protocolo {a['protocolo']} para começar a análise.", "erro")
        return redirect(url_for("fila"))
    if request.method == "POST":
        return salvar(a)
    return render_template("analise_form.html", a=a, d=calc(json.loads(a["dados"])), status=a["status"],
                           emissao=a["emissao"], analista_id=a["analista_id"], motivo=a["motivo_reprova"] or "",
                           analistas=analistas_ativos(a["analista_id"]))


def precisa_iniciar(a):
    return (not e_gestor() and a["solicitado_em"] and not a["iniciado_em"] and not a["concluido_em"])


def salvar(a):
    f = request.form
    d = dados_do_form(f)
    status = f.get("status") if f.get("status") in STATUS else "Em análise"
    emissao = f.get("emissao") if parse_data(f.get("emissao")) else date.today().isoformat()
    motivo = f.get("motivo_reprova", "") if status == "Reprovado" else ""
    finalizar = f.get("acao") == "finalizar"
    erro = None
    if status == "Reprovado" and motivo not in MOTIVOS_REPROVA:
        erro = "Informe o motivo da reprova."
    elif finalizar and status == "Em análise":
        erro = "Para finalizar, escolha o parecer no campo Status (Aprovado, Condicional, Reprovado ou Pendente)."
    if erro:
        flash(erro, "erro")
        a_view = a
        return render_template("analise_form.html", a=a_view, d=calc(d), status=status, emissao=emissao,
                               analista_id=(a["analista_id"] if a else g.user["id"]), motivo=motivo,
                               analistas=analistas_ativos(a["analista_id"] if a else None))
    analista_id = g.user["id"]
    if a:
        analista_id = a["analista_id"]
    if e_gestor() and f.get("analista_id"):
        ok = db().execute("SELECT id FROM usuarios WHERE id=? AND (ativo=1 OR id=?)",
                          (f.get("analista_id"), analista_id)).fetchone()
        if ok:
            analista_id = ok["id"]
    aid = gravar(a["id"] if a else None, a["analista_id"] if a else analista_id, status, emissao, d)
    if not a:
        log(aid, f"Análise criada com status “{status}”")
        if analista_id != g.user["id"]:
            log(aid, f"Atribuída a {nome_usuario(analista_id)} por {g.user['nome']}")
    else:
        if a["status"] != status:
            log(aid, f"Status: “{a['status']}” → “{status}”")
        else:
            log(aid, "Dados atualizados")
        if a["analista_id"] != analista_id:
            transferir_para(a, analista_id)
    if (a["motivo_reprova"] if a else "") != motivo:
        db().execute("UPDATE analises SET motivo_reprova=? WHERE id=?", (motivo, aid))
        if motivo:
            log(aid, f"Motivo da reprova: {motivo}")
    # dados do protocolo: só gestores alteram
    if e_gestor() and "protocolo" in f:
        prot = f.get("protocolo", "").strip()
        tipo = f.get("tipo") if f.get("tipo") in SLA_MIN else "LEAD"
        sol = parse_dt(f.get("solicitado_em"))
        sol_txt = sol.strftime("%Y-%m-%d %H:%M:%S") if sol else None
        antes = (a["protocolo"] or "", a["tipo"] or "LEAD", a["solicitado_em"]) if a else ("", "LEAD", None)
        if (prot, tipo, sol_txt) != antes:
            db().execute("UPDATE analises SET protocolo=?, tipo=?, solicitado_em=? WHERE id=?", (prot, tipo, sol_txt, aid))
            if antes[0] != prot:
                log(aid, f"Protocolo alterado para {prot or '(vazio)'} por {g.user['nome']}")
            if antes[1] != tipo:
                log(aid, f"Marcado como {tipo} por {g.user['nome']} (prazo {SLA_MIN[tipo]} min)")
            if antes[2] != sol_txt:
                log(aid, f"Horário de solicitação ajustado para {hora(sol_txt)} por {g.user['nome']}")
    if finalizar:
        finalizar_analise(aid)
    db().commit()
    flash("Análise finalizada. O tempo de análise e o SLA foram encerrados." if finalizar else "Análise salva.", "ok")
    if finalizar and not e_gestor():
        return redirect(url_for("fila"))
    return redirect(url_for("analise_ver", aid=aid))


@app.route("/analises/<int:aid>/iniciar", methods=["POST"])
def analise_iniciar(aid):
    a = carregar(aid)
    if a["analista_id"] != g.user["id"]:
        flash("Só o analista responsável pode iniciar este protocolo.", "erro")
        return redirect(url_for("fila"))
    if a["concluido_em"]:
        flash("Este protocolo já foi finalizado.", "erro")
        return redirect(url_for("analise_ver", aid=aid))
    if a["iniciado_em"]:
        return redirect(url_for("analise_editar", aid=aid))
    ativos = em_andamento_do(g.user["id"], aid)
    if ativos and a["tipo"] != "MESA":
        atual = ativos[0]
        flash(f"Você já está com o protocolo {atual['protocolo'] or 'nº %s' % atual['id']} em andamento. "
              "Finalize-o antes de iniciar outro. Só protocolos MESA podem ser iniciados antes.", "erro")
        return redirect(url_for("fila"))
    db().execute("UPDATE analises SET iniciado_em=?, atualizado_em=? WHERE id=?", (agora(), agora(), aid))
    espera = ""
    ini = parse_dt(a["solicitado_em"])
    if ini:
        espera = f" — {fmt_min((datetime.now() - ini).total_seconds() / 60)} após a solicitação"
    log(aid, f"Análise iniciada por {g.user['nome']}{espera}"
        + (" (MESA iniciada com outro protocolo em andamento)" if ativos else ""))
    db().commit()
    return redirect(url_for("analise_editar", aid=aid))


@app.route("/analises/<int:aid>/reabrir", methods=["POST"])
@exige("supervisor")
def analise_reabrir(aid):
    a = carregar(aid)
    if a["concluido_em"]:
        db().execute("UPDATE analises SET concluido_em=NULL, atualizado_em=? WHERE id=?", (agora(), aid))
        log(aid, f"Reaberta por {g.user['nome']} — o tempo volta a contar até nova finalização")
        db().commit()
        flash("Análise reaberta. Ela voltou para a fila.", "ok")
    return redirect(url_for("analise_ver", aid=aid))


@app.route("/analises/<int:aid>/mesa", methods=["POST"])
@exige("supervisor")
def analise_mesa(aid):
    a = carregar(aid)
    novo = "LEAD" if a["tipo"] == "MESA" else "MESA"
    db().execute("UPDATE analises SET tipo=?, atualizado_em=? WHERE id=?", (novo, agora(), aid))
    if novo == "MESA":
        log(aid, f"Marcado como MESA por {g.user['nome']} — prioridade, prazo de {SLA_MIN['MESA']} min")
    else:
        log(aid, f"Voltou para LEAD por {g.user['nome']} (prazo {SLA_MIN['LEAD']} min)")
    db().commit()
    flash(f"Protocolo {a['protocolo'] or aid} agora é {novo}.", "ok")
    return redirect(voltar(url_for("analise_ver", aid=aid)))


@app.route("/analises/<int:aid>/transferir", methods=["POST"])
@exige("supervisor")
def analise_transferir(aid):
    a = carregar(aid)
    novo = db().execute("SELECT id FROM usuarios WHERE id=? AND ativo=1", (request.form.get("analista_id"),)).fetchone()
    if not novo:
        flash("Escolha um analista ativo.", "erro")
    elif transferir_para(a, novo["id"]):
        db().commit()
        flash(f"Transferido para {nome_usuario(novo['id'])}.", "ok")
    return redirect(voltar(url_for("analise_ver", aid=aid)))


def voltar(padrao):
    nxt = request.form.get("next", "")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else padrao


@app.route("/analises/<int:aid>/duplicar", methods=["POST"])
@exige("supervisor")
def analise_duplicar(aid):
    a = carregar(aid)
    d = json.loads(a["dados"])
    novo = gravar(None, g.user["id"], "Em análise", date.today().isoformat(), d)
    log(novo, f"Criada a partir da análise nº {aid}")
    db().commit()
    flash("Cópia criada. Revise os dados.", "ok")
    return redirect(url_for("analise_editar", aid=novo))


@app.route("/analises/<int:aid>/excluir", methods=["POST"])
@exige("supervisor")
def analise_excluir(aid):
    carregar(aid)
    db().execute("DELETE FROM analises WHERE id=?", (aid,))
    db().commit()
    flash(f"Análise nº {aid} excluída.", "ok")
    return redirect(url_for("analises"))


# ------------------------------------------------------------------ distribuição e fila (SLA)


def analistas_para_fila():
    return db().execute(
        "SELECT u.id, u.nome, u.perfil,"
        " (SELECT COUNT(*) FROM analises a WHERE a.analista_id=u.id AND a.solicitado_em IS NOT NULL"
        "  AND a.concluido_em IS NULL) AS fila,"
        " (SELECT COUNT(*) FROM analises a WHERE a.analista_id=u.id AND a.solicitado_em IS NOT NULL"
        "  AND a.concluido_em IS NULL AND a.tipo='MESA') AS mesas"
        " FROM usuarios u WHERE u.ativo=1 AND u.perfil IN ('analista','supervisor') ORDER BY u.perfil, u.nome"
    ).fetchall()


@app.route("/distribuir", methods=["GET", "POST"])
@exige("supervisor")
def distribuir():
    analistas = analistas_para_fila()
    if request.method == "POST":
        f = request.form
        prot = f.get("protocolo", "").strip()
        sol = parse_dt(f.get("solicitado_em"))
        tipo = f.get("tipo") if f.get("tipo") in SLA_MIN else "LEAD"
        ids_validos = {a["id"] for a in analistas}
        try:
            analista_id = int(f.get("analista_id") or 0)
        except ValueError:
            analista_id = 0
        erro = None
        existente = None
        if not prot:
            erro = "Informe o número do protocolo do Anapro."
        elif not sol:
            erro = "Informe a data e a hora da solicitação."
        elif sol > datetime.now() + timedelta(minutes=5):
            erro = "O horário de solicitação não pode estar no futuro."
        elif analista_id not in ids_validos:
            erro = "Escolha o analista que vai receber o protocolo."
        else:
            ult = ultimo_do_protocolo(prot)
            if ult and not ult["concluido_em"]:
                erro = (f"O protocolo {prot} ainda está em andamento com {ult['analista_nome']} (análise nº {ult['id']}). "
                        "Para trocar o analista, use Transferir na fila.")
            elif ult and f.get("redistribuir") != str(ult["id"]):
                # já cadastrado: mostra os dados e pede confirmação da redistribuição
                return render_template("distribuir.html", analistas=analistas, recentes=recentes_distribuidos(),
                                       f=f, existente=ult)
            elif ult:
                existente = ult
        if erro:
            flash(erro, "erro")
            return render_template("distribuir.html", analistas=analistas, recentes=recentes_distribuidos(), f=f)
        obs_nova = f.get("obs", "").strip()
        if existente:
            d = json.loads(existente["dados"])
            if obs_nova:
                d["obs"] = (obs_nova + "\n\n" + d.get("obs", "")).strip()
        else:
            d = analise_vazia()
            d["compradores"][0]["nome"] = f.get("cliente", "").strip()
            d["imovel"]["empreendimento"] = f.get("empreendimento", "").strip()
            d["obs"] = obs_nova
        aid = gravar(None, analista_id, "Em análise", sol.date().isoformat(), d)
        db().execute("UPDATE analises SET protocolo=?, tipo=?, solicitado_em=?, distribuido_por=?, distribuido_em=?,"
                     " origem_id=? WHERE id=?", (prot, tipo, sol.strftime("%Y-%m-%d %H:%M:%S"), g.user["id"], agora(),
                                                existente["id"] if existente else None, aid))
        verbo = "redistribuído" if existente else "distribuído"
        log(aid, f"Protocolo {prot} ({tipo}) {verbo} por {g.user['nome']} para {nome_usuario(analista_id)}"
                 f" — solicitado no Anapro às {sol.strftime('%H:%M de %d/%m')}"
                 + (f" · dados reaproveitados da análise nº {existente['id']} (parecer anterior: {existente['status']})"
                    if existente else ""))
        if existente:
            db().execute("INSERT INTO historico(analise_id,usuario_id,acao,em) VALUES(?,?,?,?)",
                         (existente["id"], g.user["id"], f"Protocolo redistribuído por {g.user['nome']} — nova análise nº {aid}", agora()))
        db().commit()
        flash(f"Protocolo {prot} ({tipo}) {verbo} para {nome_usuario(analista_id)}.", "ok")
        return redirect(url_for("distribuir"))
    return render_template("distribuir.html", analistas=analistas, recentes=recentes_distribuidos(), f={})


def ultimo_do_protocolo(prot):
    return db().execute(SQL_BASE + " WHERE a.protocolo=? ORDER BY a.id DESC LIMIT 1", (prot,)).fetchone()


@app.route("/distribuir/consulta")
@exige("supervisor")
def distribuir_consulta():
    prot = request.args.get("protocolo", "").strip()
    r = ultimo_do_protocolo(prot) if prot else None
    if not r:
        return {"existe": False}
    n = db().execute("SELECT COUNT(*) FROM analises WHERE protocolo=?", (prot,)).fetchone()[0]
    return {"existe": True, "id": r["id"], "aberto": not r["concluido_em"], "cliente": r["compradores"],
            "empreendimento": r["empreendimento"], "analista": r["analista_nome"], "status": r["status"],
            "motivo": r["motivo_reprova"] or "", "tipo": r["tipo"], "solicitado": hora(r["solicitado_em"]),
            "finalizado": hora(r["concluido_em"]), "vezes": n, "url": url_for("analise_ver", aid=r["id"])}


def recentes_distribuidos():
    return db().execute(SQL_BASE + " WHERE a.distribuido_em IS NOT NULL ORDER BY a.distribuido_em DESC LIMIT 15").fetchall()


def fila_aberta(analista_id=None):
    sql = SQL_BASE + " WHERE a.solicitado_em IS NOT NULL AND a.concluido_em IS NULL"
    p = []
    if analista_id:
        sql += " AND a.analista_id=?"
        p.append(analista_id)
    itens = [(r, sla(r)) for r in db().execute(sql, p).fetchall()]
    # em andamento primeiro; depois MESA; dentro de cada grupo, quem vence antes
    itens.sort(key=lambda x: (not x[0]["iniciado_em"], x[1]["tipo"] != "MESA", x[1]["restante_seg"]))
    return itens


@app.route("/fila")
def fila():
    if e_gestor():
        filtro = request.args.get("analista", "")
        itens = fila_aberta(int(filtro) if filtro.isdigit() else None)
    else:
        filtro = ""
        itens = fila_aberta(g.user["id"])
    cont = {k: sum(1 for _, s in itens if s["estado"] == k) for k in ("no_prazo", "alerta", "atrasado")}
    mesas = sum(1 for _, s in itens if s["tipo"] == "MESA")
    concl_hoje = db().execute(SQL_BASE + " WHERE a.concluido_em >= ?" + ("" if e_gestor() else " AND a.analista_id=?"),
                              (date.today().isoformat(),) if e_gestor() else (date.today().isoformat(), g.user["id"])
                              ).fetchall()
    hoje_sla = [sla(r) for r in concl_hoje if sla(r)]
    resumo_hoje = dict(n=len(hoje_sla), no_prazo=sum(s["no_prazo"] for s in hoje_sla),
                       medio=(sum(s["decorrido_min"] for s in hoje_sla) / len(hoje_sla)) if hoje_sla else None)
    andamento = [] if e_gestor() else em_andamento_do(g.user["id"])
    return render_template("fila.html", itens=itens, cont=cont, mesas=mesas, filtro=filtro,
                           analistas=analistas_para_fila() if e_gestor() else [], resumo_hoje=resumo_hoje,
                           bloqueado=bool(andamento), andamento=andamento)


def resumo_sla(rows):
    """Indicadores de SLA para protocolos concluídos (com horário de solicitação)."""
    out = {}
    for tipo in ("LEAD", "MESA", None):
        ss = [s for s in (sla(r) for r in rows) if s and s["concluido"] and (tipo is None or s["tipo"] == tipo)]
        tt = [t["minutos"] for r in rows for t in [tempo_analise(r)]
              if t and not t["em_andamento"] and (tipo is None or (r["tipo"] or "LEAD") == tipo)]
        out[tipo or "TOTAL"] = dict(
            n=len(ss), no_prazo=sum(s["no_prazo"] for s in ss),
            pct=(sum(s["no_prazo"] for s in ss) / len(ss) * 100) if ss else None,
            medio=(sum(s["decorrido_min"] for s in ss) / len(ss)) if ss else None,
            analise_n=len(tt), analise=(sum(tt) / len(tt)) if tt else None)
    return out


# ------------------------------------------------------------------ painel (supervisor+)


def periodo_args(padrao_dias=None):
    hoje = date.today()
    ini_padrao = hoje.replace(day=1) if padrao_dias is None else hoje - timedelta(days=padrao_dias)
    de = parse_data(request.args.get("de"), ini_padrao)
    ate = parse_data(request.args.get("ate"), hoje)
    if de > ate:
        de, ate = ate, de
    return de, ate


def todas_analises():
    return db().execute(SQL_BASE).fetchall()


@app.route("/painel")
@exige("supervisor")
def painel():
    de, ate = periodo_args()
    hoje = date.today()
    rows = todas_analises()
    no_periodo = [r for r in rows if de.isoformat() <= r["emissao"] <= ate.isoformat()]

    kpi = {
        "periodo": len(no_periodo),
        "aprovadas": sum(r["status"] in STATUS_APROVADOS for r in no_periodo),
        "reprovadas": sum(r["status"] == "Reprovado" for r in no_periodo),
        "em_analise": sum(r["status"] == "Em análise" for r in rows),
        "pendentes": sum(r["status"] == "Pendente documentação" for r in rows),
    }
    mes_ini = hoje.replace(day=1).isoformat()
    do_mes = [r for r in rows if r["emissao"] >= mes_ini and r["emissao"] <= hoje.isoformat()]
    kpi["volume_mes"] = sum(r["total_aprovado"] for r in do_mes if r["status"] in STATUS_APROVADOS)
    kpi["aprovadas_mes"] = sum(r["status"] in STATUS_APROVADOS for r in do_mes)
    kpi["mes_rotulo"] = f"{MESES[hoje.month - 1]}/{hoje.year}"

    # SLA
    fila_agora = fila_aberta()
    sla_agora = {k: sum(1 for _, s in fila_agora if s["estado"] == k) for k in ("no_prazo", "alerta", "atrasado")}
    sla_agora["mesas"] = sum(1 for _, s in fila_agora if s["tipo"] == "MESA")
    sla_agora["total"] = len(fila_agora)
    sla_periodo = resumo_sla(no_periodo)

    # produção por analista
    usuarios = db().execute("SELECT id,nome,perfil,ultimo_acesso FROM usuarios WHERE ativo=1 ORDER BY nome").fetchall()
    equipe = []
    for u in usuarios:
        mine = [r for r in no_periodo if r["analista_id"] == u["id"]]
        abertos = [r for r in rows if r["analista_id"] == u["id"] and r["status"] in STATUS_ABERTOS]
        na_fila = [s for r, s in fila_agora if r["analista_id"] == u["id"]]
        if u["perfil"] != "analista" and not mine and not abertos and not na_fila:
            continue
        rs = resumo_sla(mine)["TOTAL"]
        equipe.append(dict(
            id=u["id"], nome=u["nome"], perfil=u["perfil"], ultimo=u["ultimo_acesso"],
            total=len(mine),
            aprovadas=sum(r["status"] in STATUS_APROVADOS for r in mine),
            reprovadas=sum(r["status"] == "Reprovado" for r in mine),
            abertas=len(abertos),
            volume=sum(r["total_aprovado"] for r in do_mes
                       if r["analista_id"] == u["id"] and r["status"] in STATUS_APROVADOS),
            andamento=[x for x in rows if x["analista_id"] == u["id"] and x["iniciado_em"] and not x["concluido_em"]],
            analise_medio=rs["analise"], analise_n=rs["analise_n"],
            fila=len(na_fila), fila_atrasados=sum(s["estado"] == "atrasado" for s in na_fila),
            fila_mesas=sum(s["tipo"] == "MESA" for s in na_fila),
            sla_n=rs["n"], sla_medio=rs["medio"], sla_pct=rs["pct"]))
    equipe.sort(key=lambda e: (-e["total"], e["nome"]))
    maior = max([e["total"] for e in equipe] + [1])

    # alertas
    alertas = []
    for r in rows:
        v = parse_data(r["vencimento_sicaq"])
        if v and r["status"] != "Reprovado":
            dias = (v - hoje).days
            if dias < 0:
                alertas.append((0, r, f"SICAQ vencido há {-dias} dia(s)", "bad"))
            elif dias <= 30:
                alertas.append((1, r, f"SICAQ vence em {dias} dia(s) — {data_br(r['vencimento_sicaq'])}", "warn"))
        if r["status"] in STATUS_ABERTOS:
            parado = (datetime.now() - datetime.strptime(r["atualizado_em"], "%Y-%m-%d %H:%M:%S")).days
            if parado >= 5:
                alertas.append((2, r, f"{r['status']} sem movimentação há {parado} dias", "warn"))
    alertas.sort(key=lambda x: (x[0], x[1]["vencimento_sicaq"] or ""))

    return render_template("painel.html", de=de.isoformat(), ate=ate.isoformat(), kpi=kpi, equipe=equipe,
                           maior=maior, alertas=alertas[:40], n_alertas=len(alertas), sla_agora=sla_agora,
                           sla_periodo=sla_periodo, fila_agora=fila_agora[:8])


# ------------------------------------------------------------------ relatórios (diretor)

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


@app.route("/relatorios")
@exige("diretor")
def relatorios():
    de, ate = periodo_args(padrao_dias=None)
    if not request.args.get("de"):
        de = date(date.today().year, 1, 1)
    rows = todas_analises()
    sel = [r for r in rows if de.isoformat() <= r["emissao"] <= ate.isoformat()]
    aprov = [r for r in sel if r["status"] in STATUS_APROVADOS]
    decididas = len(aprov) + sum(r["status"] == "Reprovado" for r in sel)
    reprov = [r for r in sel if r["status"] == "Reprovado"]
    comps = [c for r in sel for c in json.loads(r["dados"]).get("compradores", [])]
    proc_inf = sum(1 for r in sel if any(money(c.get("informal")) > 0 for c in json.loads(r["dados"]).get("compradores", [])))
    proc_for = sum(1 for r in sel if any(money(c.get("formal")) > 0 for c in json.loads(r["dados"]).get("compradores", [])))
    kpi = dict(
        total=len(sel), aprovadas=len(aprov), reprovadas=len(reprov),
        taxa=(len(aprov) / decididas * 100) if decididas else None,
        proc_informal=proc_inf, comp_informal=sum(1 for c in comps if money(c.get("informal")) > 0),
        proc_formal=proc_for, comp_formal=sum(1 for c in comps if money(c.get("formal")) > 0),
        abertas=sum(r["status"] in STATUS_ABERTOS for r in sel))

    # motivos de reprova
    motivo_f = request.args.get("motivo", "")
    por_motivo = [(m, sum(r["motivo_reprova"] == m for r in reprov)) for m in MOTIVOS_REPROVA]
    sem_motivo = sum(not r["motivo_reprova"] for r in reprov)
    if sem_motivo:
        por_motivo.append(("Não informado", sem_motivo))
    max_motivo = max([n for _, n in por_motivo] + [1])
    if motivo_f == "Não informado":
        lista_reprov = [r for r in reprov if not r["motivo_reprova"]]
    elif motivo_f:
        lista_reprov = [r for r in reprov if r["motivo_reprova"] == motivo_f]
    else:
        lista_reprov = reprov
    lista_reprov = sorted(lista_reprov, key=lambda r: r["emissao"], reverse=True)
    motivo_analista = {}
    for r in reprov:
        e = motivo_analista.setdefault(r["analista_nome"], {m: 0 for m in MOTIVOS_REPROVA + ["Não informado"]})
        e[r["motivo_reprova"] or "Não informado"] += 1

    # status
    por_status = [(s, sum(r["status"] == s for r in sel)) for s in STATUS]
    max_status = max([n for _, n in por_status] + [1])

    # mês a mês (últimos 12 meses até 'ate')
    meses = []
    y, m = ate.year, ate.month
    for _ in range(12):
        meses.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    meses.reverse()
    serie = []
    for (y, m) in meses:
        chave = f"{y:04d}-{m:02d}"
        rs = [r for r in rows if r["emissao"][:7] == chave]
        serie.append(dict(rotulo=f"{MESES[m-1]}/{str(y)[2:]}", total=len(rs),
                          reprovadas=sum(r["status"] == "Reprovado" for r in rs),
                          aprovadas=sum(r["status"] in STATUS_APROVADOS for r in rs),
                          condicionais=sum(r["status"] == "Aprovado - Condicional" for r in rs)))
    max_mes = max([s["total"] for s in serie] + [1])
    max_rep = max([s["reprovadas"] for s in serie] + [1])
    max_apr = max([s["aprovadas"] for s in serie] + [1])

    # por analista
    agg = {}
    for r in sel:
        e = agg.setdefault(r["analista_id"], dict(nome=r["analista_nome"], perfil=r["analista_perfil"], total=0,
                                                   aprovadas=0, reprovadas=0, abertas=0, volume=0.0))
        e["total"] += 1
        if r["status"] in STATUS_APROVADOS:
            e["aprovadas"] += 1
            e["volume"] += r["total_aprovado"]
        elif r["status"] == "Reprovado":
            e["reprovadas"] += 1
        else:
            e["abertas"] += 1
    for aid_, e in agg.items():
        rs = resumo_sla([r for r in sel if r["analista_id"] == aid_])
        e["sla"] = rs
    ranking = sorted(agg.values(), key=lambda e: (-e["total"], e["nome"]))
    for e in ranking:
        dec = e["aprovadas"] + e["reprovadas"]
        e["taxa"] = (e["aprovadas"] / dec * 100) if dec else None

    # SLA geral e mês a mês
    sla_geral = resumo_sla(sel)
    sla_mes = []
    for (y, m) in meses:
        chave = f"{y:04d}-{m:02d}"
        rs = resumo_sla([r for r in rows if r["emissao"][:7] == chave])["TOTAL"]
        sla_mes.append(dict(rotulo=f"{MESES[m-1]}/{str(y)[2:]}", **rs))

    # quem distribuiu
    dist = {}
    for r in sel:
        if r["distribuido_por"]:
            e = dist.setdefault(r["distribuido_por"], dict(nome=r["distribuidor_nome"], total=0, mesas=0))
            e["total"] += 1
            e["mesas"] += r["tipo"] == "MESA"
    distribuidores = sorted(dist.values(), key=lambda e: -e["total"])
    max_rank = max([e["total"] for e in ranking] + [1])

    # por construtora
    cons = {}
    for r in sel:
        k = r["construtora"] or "Não informada"
        c = cons.setdefault(k, dict(nome=k, total=0, aprovadas=0, volume=0.0))
        c["total"] += 1
        if r["status"] in STATUS_APROVADOS:
            c["aprovadas"] += 1
            c["volume"] += r["total_aprovado"]
    construtoras = sorted(cons.values(), key=lambda c: (-c["total"], -c["volume"]))[:10]
    max_cons = max([c["total"] for c in construtoras] + [1])

    return render_template("relatorios.html", de=de.isoformat(), ate=ate.isoformat(), kpi=kpi,
                           por_status=por_status, max_status=max_status, serie=serie, max_mes=max_mes,
                           max_rep=max_rep, max_apr=max_apr, por_motivo=por_motivo, max_motivo=max_motivo, motivo_f=motivo_f,
                           lista_reprov=lista_reprov[:100], n_lista_reprov=len(lista_reprov),
                           motivo_analista=motivo_analista, ranking=ranking, max_rank=max_rank, construtoras=construtoras,
                           max_cons=max_cons, sla_geral=sla_geral, sla_mes=sla_mes, distribuidores=distribuidores)


@app.route("/relatorios/exportar.csv")
@exige("supervisor")
def exportar_csv():
    de, ate = periodo_args()
    if not request.args.get("de"):
        de = date(2000, 1, 1)
    rows = [r for r in todas_analises() if de.isoformat() <= r["emissao"] <= ate.isoformat()]
    rows.sort(key=lambda r: r["emissao"])
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Nº", "Protocolo", "Tipo", "Solicitado em", "Distribuído por", "Distribuído em", "Iniciado em",
                "Finalizado em", "Tempo de análise (min)", "SLA total (min)", "Situação SLA", "Motivo da reprova",
                "Emissão", "Analista", "Status", "Construtora", "Empreendimento", "Compradores",
                "Renda familiar", "Financiamento", "FGTS", "Subsídio", "Recursos próprios", "Total aprovado",
                "Parcela", "Prazo", "Enquadramento", "Faixa", "Vencimento SICAQ", "Atualizado em"])
    for r in rows:
        d = json.loads(r["dados"])
        fin = d["fin"]
        s = sla(r)
        t = tempo_analise(r)
        w.writerow([r["id"], r["protocolo"], r["tipo"] if r["solicitado_em"] else "", data_hora_br(r["solicitado_em"]),
                    r["distribuidor_nome"] or "", data_hora_br(r["distribuido_em"]), data_hora_br(r["iniciado_em"]),
                    data_hora_br(r["concluido_em"]), round(t["minutos"]) if t else "",
                    round(s["decorrido_min"]) if s else "", s["rotulo"] if s else "", r["motivo_reprova"] or "",
                    data_br(r["emissao"]), r["analista_nome"], r["status"], r["construtora"],
                    r["empreendimento"], r["compradores"], brl(r["renda_familiar"], False),
                    brl(fin.get("financiamento"), False), brl(fin.get("fgts"), False),
                    brl(fin.get("subsidio"), False), brl(fin.get("proprios"), False),
                    brl(r["total_aprovado"], False), brl(fin.get("parcela"), False), fin.get("prazo"),
                    fin.get("enquadramento"), d.get("faixa"), data_br(r["vencimento_sicaq"]),
                    data_hora_br(r["atualizado_em"])])
    nome = f"analises_{de.isoformat()}_{ate.isoformat()}.csv"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={nome}"})


@app.route("/backup")
@exige("diretor")
def backup():
    tmp = os.path.join(tempfile.gettempdir(), f"yourhome_crm_backup_{date.today().isoformat()}.db")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    dst.close()
    src.close()
    return send_file(tmp, as_attachment=True, download_name=os.path.basename(tmp))


# ------------------------------------------------------------------ usuários


def pode_gerir(perfil_alvo):
    return perfil_alvo in GERENCIA.get(g.user["perfil"], [])


def pode_editar(perfil_alvo):
    return pode_gerir(perfil_alvo) or perfil_alvo in EDITA_EXTRA.get(g.user["perfil"], [])


@app.route("/usuarios")
@exige("supervisor")
def usuarios():
    rows = db().execute("SELECT u.*, (SELECT COUNT(*) FROM analises a WHERE a.analista_id=u.id) AS n"
                        " FROM usuarios u ORDER BY u.ativo DESC, u.nome").fetchall()
    return render_template("usuarios.html", rows=rows, pode_gerir=pode_gerir, pode_editar=pode_editar)


@app.route("/usuarios/novo", methods=["GET", "POST"])
@app.route("/usuarios/<int:uid>/editar", methods=["GET", "POST"])
@exige("supervisor")
def usuario_form(uid=None):
    u = None
    if uid:
        u = db().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
        if not u:
            abort(404)
        if not pode_editar(u["perfil"]):
            abort(403)
    opcoes = GERENCIA[g.user["perfil"]]
    if u and not pode_gerir(u["perfil"]):
        opcoes = [u["perfil"]]  # edita só os dados; o perfil fica como está
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cpf = so_digitos(request.form.get("cpf"))
        email = request.form.get("email", "").strip()
        perfil = request.form.get("perfil")
        erro = None
        if not nome:
            erro = "Informe o nome."
        elif not cpf_valido(cpf):
            erro = "CPF inválido."
        elif perfil not in opcoes:
            erro = "Perfil não permitido."
        else:
            outro = db().execute("SELECT id FROM usuarios WHERE cpf=? AND id<>?", (cpf, uid or 0)).fetchone()
            if outro:
                erro = "Já existe um usuário com este CPF."
        if u and u["id"] == g.user["id"] and perfil != u["perfil"]:
            erro = "Você não pode alterar o próprio perfil."
        if erro:
            flash(erro, "erro")
        else:
            if u:
                db().execute("UPDATE usuarios SET nome=?,cpf=?,email=?,perfil=? WHERE id=?",
                             (nome, cpf, email, perfil, uid))
                flash("Usuário atualizado.", "ok")
            else:
                db().execute("INSERT INTO usuarios(cpf,nome,email,perfil,senha_hash,trocar_senha,criado_em)"
                             " VALUES(?,?,?,?,?,1,?)",
                             (cpf, nome, email, perfil, generate_password_hash(SENHA_PADRAO), agora()))
                flash(f"Usuário criado. Login: {fmt_cpf(cpf)} · senha inicial: {SENHA_PADRAO} "
                      "(será pedida a troca no primeiro acesso).", "ok")
            db().commit()
            return redirect(url_for("usuarios"))
    return render_template("usuario_form.html", u=u, opcoes=opcoes, f=request.form)


@app.route("/usuarios/<int:uid>/resetar", methods=["POST"])
@exige("supervisor")
def usuario_resetar(uid):
    u = db().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    if not pode_gerir(u["perfil"]) or u["id"] == g.user["id"]:
        abort(403)
    db().execute("UPDATE usuarios SET senha_hash=?, trocar_senha=1, tentativas=0, bloqueado_ate=NULL WHERE id=?",
                 (generate_password_hash(SENHA_PADRAO), uid))
    db().commit()
    flash(f"Senha de {u['nome']} redefinida para {SENHA_PADRAO}. A troca será pedida no próximo acesso.", "ok")
    return redirect(url_for("usuarios"))


@app.route("/usuarios/<int:uid>/excluir", methods=["GET", "POST"])
@exige("supervisor")
def usuario_excluir(uid):
    u = db().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    if not pode_gerir(u["perfil"]) or u["id"] == g.user["id"]:
        abort(403)
    n = db().execute("SELECT COUNT(*) FROM analises WHERE analista_id=?", (uid,)).fetchone()[0]
    abertas = db().execute("SELECT COUNT(*) FROM analises WHERE analista_id=? AND concluido_em IS NULL", (uid,)).fetchone()[0]
    destinos = db().execute("SELECT id,nome,perfil FROM usuarios WHERE ativo=1 AND id<>? ORDER BY nome", (uid,)).fetchall()
    if request.method == "POST":
        if so_digitos(request.form.get("confirma_cpf")) != u["cpf"]:
            flash("Para confirmar, digite o CPF do usuário que será excluído.", "erro")
            return render_template("usuario_excluir.html", u=u, n=n, abertas=abertas, destinos=destinos)
        destino = None
        if n:
            destino = db().execute("SELECT id,nome FROM usuarios WHERE id=? AND ativo=1 AND id<>?",
                                   (request.form.get("destino_id"), uid)).fetchone()
            if not destino:
                flash("Escolha para quem vão as análises deste usuário.", "erro")
                return render_template("usuario_excluir.html", u=u, n=n, abertas=abertas, destinos=destinos)
            ids = [r[0] for r in db().execute("SELECT id FROM analises WHERE analista_id=?", (uid,))]
            db().execute("UPDATE analises SET analista_id=?, iniciado_em=CASE WHEN concluido_em IS NULL THEN NULL"
                         " ELSE iniciado_em END WHERE analista_id=?", (destino["id"], uid))
            for aid in ids:
                log(aid, f"Análise passada de {u['nome']} para {destino['nome']} por {g.user['nome']} (usuário excluído)")
        db().execute("UPDATE historico SET usuario_id=NULL WHERE usuario_id=?", (uid,))
        db().execute("DELETE FROM usuarios WHERE id=?", (uid,))
        db().commit()
        flash(f"Usuário {u['nome']} excluído." + (f" {n} análise(s) passaram para {destino['nome']}." if destino else ""), "ok")
        return redirect(url_for("usuarios"))
    return render_template("usuario_excluir.html", u=u, n=n, abertas=abertas, destinos=destinos)


@app.route("/usuarios/<int:uid>/ativo", methods=["POST"])
@exige("supervisor")
def usuario_ativo(uid):
    u = db().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    if not pode_gerir(u["perfil"]) or u["id"] == g.user["id"]:
        abort(403)
    novo = 0 if u["ativo"] else 1
    db().execute("UPDATE usuarios SET ativo=? WHERE id=?", (novo, uid))
    db().commit()
    flash(f"{u['nome']} {'reativado' if novo else 'desativado'}.", "ok")
    return redirect(url_for("usuarios"))


# ------------------------------------------------------------------ execução

init_db()

if __name__ == "__main__":
    import socket
    porta = int(os.environ.get("PORT", 5000))
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "127.0.0.1"
    print("\n  Your Home — CRM de Análises de Crédito")
    print(f"  Neste computador:   http://localhost:{porta}")
    print(f"  Na rede da empresa: http://{ip}:{porta}\n")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=porta, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=porta)
