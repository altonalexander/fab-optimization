"""
fabslate -- ctypes binding to dispatch/libfabslate.so.

This is the whole Python/C++ boundary. See docs/adr/0009 for why it is a C ABI
rather than pybind11 (neither pybind11 nor Python development headers are
required, and the boundary is coarse enough that pybind11's ergonomics would
buy little).

The boundary is crossed ONCE PER PLANNING CYCLE, not once per decision point.
A 730-day LVHM run has ~16M decision points but only ~1M planning cycles at
N=60s, and the decision points read the returned snapshot without crossing at
all. That is the property that makes ctypes fast enough.

Struct layouts here must match slate_capi.cpp exactly. They are checked against
fabslate_sizeof() at load time rather than trusted, because a padding
disagreement does not crash -- it silently misreads every field after the first
divergence, which would look like a bad dispatching rule rather than a bug.
"""
import ctypes
import os

ID = 48
NAME = 72

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
DEFAULT_LIB = os.path.join(REPO, 'dispatch', 'libfabslate.so')


class CTool(ctypes.Structure):
    _fields_ = [
        ('tool_id',        ctypes.c_char * ID),
        ('family',         ctypes.c_char * ID),
        ('current_setup',  ctypes.c_char * ID),
        ('capacity',       ctypes.c_int),
        ('online',         ctypes.c_int),
        ('speed',          ctypes.c_double),
        ('min_run_length', ctypes.c_int),
        ('min_runs_left',  ctypes.c_int),
        ('min_runs_setup', ctypes.c_char * ID),
    ]


class CLot(ctypes.Structure):
    _fields_ = [
        ('lot_id',         ctypes.c_char * ID),
        ('family',         ctypes.c_char * ID),
        ('setup_group',    ctypes.c_char * ID),
        ('step',           ctypes.c_char * NAME),
        ('part',           ctypes.c_char * ID),
        ('batch_min',      ctypes.c_int),
        ('batch_max',      ctypes.c_int),
        ('wafers',         ctypes.c_int),
        ('priority',       ctypes.c_double),
        ('qtime_slack_s',  ctypes.c_double),
        ('step_process_s', ctypes.c_double),
        ('due_s',          ctypes.c_double),
        ('waiting_s',      ctypes.c_double),
    ]


class CToken(ctypes.Structure):
    _fields_ = [
        ('lot_index',          ctypes.c_int),
        ('tool_id',            ctypes.c_char * ID),
        ('alternate',          ctypes.c_char * ID),
        ('rank',               ctypes.c_int),
        ('expected_process_s', ctypes.c_double),
    ]


class CPlanStats(ctypes.Structure):
    _fields_ = [
        ('assigned',     ctypes.c_int),
        ('ready',        ctypes.c_int),
        ('variables',    ctypes.c_int),
        ('solve_time_s', ctypes.c_double),
        ('objective',    ctypes.c_double),
        ('status',       ctypes.c_int),
        ('detail',       ctypes.c_char * 256),
    ]


# fab::SolveStatus
STATUS = {0: 'optimal', 1: 'feasible', 2: 'infeasible', 3: 'no_incumbent', 4: 'error'}


class LibraryMissing(RuntimeError):
    pass


class LayoutMismatch(RuntimeError):
    pass


class Planner:
    """One planner handle. Not thread-safe; make one per simulation."""

    def __init__(self, solver='cpsat', lib_path=None):
        path = lib_path or os.environ.get('FABSLATE_LIB') or DEFAULT_LIB
        if not os.path.exists(path):
            raise LibraryMissing(
                f'{path} not found -- build it with dispatch/build-slate.sh')
        self._lib = ctypes.CDLL(path)
        self._bind()
        self._check_layout()

        self._h = self._lib.fabslate_new(solver.encode())
        if not self._h:
            raise RuntimeError('fabslate_new returned NULL')
        self.solver = self._lib.fabslate_solver_name(self._h).decode()
        # False means OR-Tools was not linked and every "cpsat" result is
        # really the greedy fallback. Callers must surface this, not ignore it.
        self.solver_available = bool(self._lib.fabslate_solver_available(self._h))
        self._n_tools = 0

    # -- binding ------------------------------------------------------------
    def _bind(self):
        L = self._lib
        L.fabslate_new.argtypes = [ctypes.c_char_p]
        L.fabslate_new.restype = ctypes.c_void_p
        L.fabslate_free.argtypes = [ctypes.c_void_p]
        L.fabslate_free.restype = None
        L.fabslate_solver_name.argtypes = [ctypes.c_void_p]
        L.fabslate_solver_name.restype = ctypes.c_char_p
        L.fabslate_solver_available.argtypes = [ctypes.c_void_p]
        L.fabslate_solver_available.restype = ctypes.c_int
        L.fabslate_set_setup.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                         ctypes.c_char_p, ctypes.c_double]
        L.fabslate_set_setup.restype = None
        L.fabslate_set_setup_default.argtypes = [ctypes.c_void_p, ctypes.c_double]
        L.fabslate_set_setup_default.restype = None
        L.fabslate_set_tools.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(CTool), ctypes.c_int]
        L.fabslate_set_tools.restype = ctypes.c_int
        L.fabslate_update_tools.argtypes = [ctypes.c_void_p,
                                            ctypes.POINTER(CTool), ctypes.c_int]
        L.fabslate_update_tools.restype = ctypes.c_int
        L.fabslate_plan.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(CLot), ctypes.c_int,
            ctypes.c_double, ctypes.c_double, ctypes.c_int, ctypes.c_char_p,
            ctypes.POINTER(CToken), ctypes.c_int, ctypes.POINTER(CPlanStats)]
        L.fabslate_plan.restype = ctypes.c_int
        L.fabslate_sizeof.argtypes = [ctypes.c_int]
        L.fabslate_sizeof.restype = ctypes.c_int

    def _check_layout(self):
        for i, (name, cls) in enumerate(
                [('CTool', CTool), ('CLot', CLot),
                 ('CToken', CToken), ('CPlanStats', CPlanStats)]):
            want = self._lib.fabslate_sizeof(i)
            got = ctypes.sizeof(cls)
            if want != got:
                raise LayoutMismatch(
                    f'{name}: libfabslate.so says {want} bytes, Python says {got}. '
                    'slate_capi.cpp and fabslate.py have diverged -- rebuild, and '
                    'if that does not fix it the field lists no longer match.')

    # -- configuration ------------------------------------------------------
    def set_setup_matrix(self, pairs, default_s=0.0):
        """pairs: iterable of (from_setup, to_setup, seconds).

        SMT2020's setup.txt is ASYMMETRIC -- A->B need not equal B->A -- so
        callers must not collapse it into a symmetric table.
        """
        self._lib.fabslate_set_setup_default(self._h, float(default_s))
        for frm, to, sec in pairs:
            self._lib.fabslate_set_setup(
                self._h, str(frm).encode(), str(to).encode(), float(sec))

    def set_tools(self, tools):
        """tools: list of dicts. Registers the tool SET; call once.

        The ctypes array is KEPT. Rebuilding it every cycle meant constructing
        1,313 Python dicts and refilling 1,313 structs ~1,440 times a simulated
        day, which dominated the run. Callers mutate the retained array in
        place through set_tool_state() and then flush_tools().
        """
        arr = (CTool * len(tools))()
        for i, t in enumerate(tools):
            _fill_tool(arr[i], t)
        n = self._lib.fabslate_set_tools(self._h, arr, len(tools))
        if n < 0:
            raise RuntimeError(f'fabslate_set_tools failed ({n})')
        self._n_tools = n
        self._tool_arr = arr
        return n

    def set_tool_state(self, i, online=None, current_setup=None,
                       min_runs_left=None, min_runs_setup=None):
        """Mutate one tool's state in the retained array. Cheap; no C call."""
        c = self._tool_arr[i]
        if online is not None:
            c.online = 1 if online else 0
        if current_setup is not None:
            c.current_setup = _b(current_setup, ID)
        if min_runs_left is not None:
            c.min_runs_left = int(min_runs_left)
        if min_runs_setup is not None:
            c.min_runs_setup = _b(min_runs_setup, ID)

    def flush_tools(self):
        """Push the retained array to C++. One memcpy-shaped call per cycle."""
        n = self._lib.fabslate_update_tools(self._h, self._tool_arr, self._n_tools)
        if n < 0:
            raise RuntimeError(f'fabslate_update_tools failed ({n})')
        return n

    def update_tools(self, tools):
        """Per-cycle state refresh from dicts. POSITIONAL: set_tools() order.

        Kept for callers that have not adopted set_tool_state(); it is the slow
        path and rebuilds every struct.
        """
        arr = (CTool * len(tools))()
        for i, t in enumerate(tools):
            _fill_tool(arr[i], t)
        n = self._lib.fabslate_update_tools(self._h, arr, len(tools))
        if n == -2:
            raise RuntimeError(
                f'update_tools got {len(tools)} tools, registry holds '
                f'{self._n_tools}; the tool set must be static for a run')
        if n < 0:
            raise RuntimeError(f'fabslate_update_tools failed ({n})')
        return n

    # -- plan ---------------------------------------------------------------
    def plan(self, lots, budget_s=0.05, relative_gap=0.02, threads=1,
             dirty_families=None):
        """Solve and return (tokens, stats).

        tokens: list of (lot_index, tool_id, alternate, rank, expected_s),
        where lot_index indexes the `lots` list that was passed in.

        dirty_families: when given, only these families are re-solved and the
        rest are carried forward from the previous slate.
        """
        n = len(lots)
        arr = (CLot * n)() if n else (CLot * 1)()
        for i, l in enumerate(lots):
            _fill_lot(arr[i], l)

        out = (CToken * max(n, 1))()
        st = CPlanStats()
        dirty = None
        if dirty_families is not None:
            dirty = '\n'.join(dirty_families).encode()

        got = self._lib.fabslate_plan(
            self._h, arr, n, float(budget_s), float(relative_gap), int(threads),
            dirty, out, max(n, 1), ctypes.byref(st))
        if got < 0:
            raise RuntimeError(f'fabslate_plan failed ({got})')

        tokens = [(out[i].lot_index,
                   out[i].tool_id.decode(),
                   out[i].alternate.decode(),
                   out[i].rank,
                   out[i].expected_process_s) for i in range(got)]
        stats = {
            'assigned': st.assigned, 'ready': st.ready,
            'variables': st.variables, 'solve_time_s': st.solve_time_s,
            'objective': st.objective,
            'status': STATUS.get(st.status, str(st.status)),
            'detail': st.detail.decode(),
        }
        return tokens, stats

    def close(self):
        if getattr(self, '_h', None):
            self._lib.fabslate_free(self._h)
            self._h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _b(s, n):
    """Encode and truncate to fit a fixed-width field, leaving room for NUL.

    Truncation is silent by design: an over-long identifier should degrade the
    plan, not abort a 730-day run. The widths are generous against SMT2020.
    """
    return str(s).encode()[:n - 1]


def _fill_tool(c, t):
    c.tool_id = _b(t['tool_id'], ID)
    c.family = _b(t['family'], ID)
    c.current_setup = _b(t.get('current_setup', ''), ID)
    c.capacity = int(t.get('capacity', 1))
    c.online = 1 if t.get('online', True) else 0
    c.speed = float(t.get('speed', 1.0))
    c.min_run_length = int(t.get('min_run_length', 0))
    c.min_runs_left = int(t.get('min_runs_left', 0))
    c.min_runs_setup = _b(t.get('min_runs_setup', ''), ID)


def _fill_lot(c, l):
    c.lot_id = _b(l['lot_id'], ID)
    c.family = _b(l['family'], ID)
    c.setup_group = _b(l.get('setup_group', ''), ID)
    c.step = _b(l.get('step', ''), NAME)
    c.part = _b(l.get('part', ''), ID)
    c.batch_min = int(l.get('batch_min', 1))
    c.batch_max = int(l.get('batch_max', 1))
    c.wafers = int(l.get('wafers', 25))
    c.priority = float(l.get('priority', 1.0))
    c.qtime_slack_s = float(l.get('qtime_slack_s', 1e9))
    c.step_process_s = float(l.get('step_process_s', 0.0))
    c.due_s = float(l.get('due_s', -1.0))
    c.waiting_s = float(l.get('waiting_s', 0.0))
