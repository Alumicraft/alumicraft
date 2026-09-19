import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Loader2,
  LogOut,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";
import {
  api,
  isActiveStatus,
  isEditableStatus,
  isSourceEditable,
  LOGIN_URL,
  PortalApiError,
  toSavePayload,
} from "@/lib/api";
import type {
  BootstrapResponse,
  LaborRow,
  MaterialRow,
  ProjectResult,
  Study,
  StudySummary,
} from "@/lib/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Textarea } from "@/components/ui/textarea";

const PAGE_SIZE = 50;
const ASSEMBLIES = [
  "Chassis and fabrication",
  "Suspension",
  "Drivetrain",
  "Brakes and steering",
  "Electrical",
  "Cooling and fuel",
  "Interior and body",
  "Wheels and tires",
  "Consumables",
  "Unknown",
];
const PURPOSES = ["Standard", "Custom", "Spare", "Rework", "Unknown"];
const REVIEWS = ["Pending", "Approved", "Excluded"];

function blankStudy(bootstrap: BootstrapResponse | null): Study {
  const company = bootstrap?.companies[0];
  return {
    name: "",
    title: "",
    company: company?.name || "",
    currency: company?.currency || "USD",
    standard_description: "",
    mode: "Historical only",
    from_date: "",
    to_date: "",
    status: "Draft",
    projects: [],
    materials: [],
    labor: [],
    material_allowance: 0,
    overhead_allowance: 0,
    target_margin: 0,
  };
}
function statusVariant(
  status: string,
): "default" | "secondary" | "outline" | "destructive" {
  return status === "Needs Review"
    ? "default"
    : status === "Failed"
      ? "destructive"
      : status === "Draft"
        ? "outline"
        : "secondary";
}
function money(value: number | null | undefined, currency = "USD") {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(Number(value) || 0);
  } catch {
    return `${currency} ${Number(value) || 0}`;
  }
}
function dateLabel(value?: string | null) {
  if (!value) return "No date";
  const date = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}
function manualRow(row: MaterialRow | LaborRow) {
  return Boolean(
    row.isManual ||
    (!row.name && !("line_key" in row && row.line_key) && !("amount" in row)),
  );
}
function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong.";
}

export default function App() {
  const [bootstrap, setBootstrap] = useState<BootstrapResponse | null>(null);
  const [studies, setStudies] = useState<StudySummary[]>([]);
  const [study, setStudy] = useState<Study | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{
    kind: "error" | "success";
    text: string;
  } | null>(null);
  const [authError, setAuthError] = useState(false);
  const [projectQuery, setProjectQuery] = useState("");
  const [projectResults, setProjectResults] = useState<ProjectResult[]>([]);
  const [activeTab, setActiveTab] = useState<"materials" | "labor">(
    "materials",
  );
  const [page, setPage] = useState(0);
  const [evidence, setEvidence] = useState<unknown>(null);
  const projectSearchVersion = useRef(0);
  const selectionVersion = useRef(0);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const [navigation, setNavigation] = useState<(() => void) | null>(null);
  const navigate = (action: () => void) => {
    if (saving || loading) return;
    if (dirty) setNavigation(() => action);
    else action();
  };
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const reportError = useCallback((error: unknown) => {
    if (error instanceof PortalApiError && error.kind === "auth")
      setAuthError(true);
    else setMessage({ kind: "error", text: errorMessage(error) });
  }, []);
  const refreshList = useCallback(async () => {
    try {
      setStudies(await api.listStudies());
      return true;
    } catch (error) {
      reportError(error);
      return false;
    }
  }, [reportError]);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [boot, initialStudies] = await Promise.all([
          api.bootstrap(),
          api.listStudies(),
        ]);
        if (!alive) return;
        setBootstrap(boot);
        setStudies(initialStudies);
        setLoading(false);
      } catch (error) {
        if (alive) {
          setLoading(false);
          reportError(error);
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [reportError]);

  const chooseStudy = async (name: string) => {
    const version = ++selectionVersion.current;
    ++projectSearchVersion.current;
    setLoading(true);
    setMessage(null);
    setProjectResults([]);
    setProjectQuery("");
    try {
      const incoming = await api.getStudy(name);
      if (version !== selectionVersion.current) return;
      setStudy(incoming);
      setDirty(false);
      setPage(0);
      setActiveTab("materials");
    } catch (error) {
      if (version === selectionVersion.current) reportError(error);
    } finally {
      if (version === selectionVersion.current) setLoading(false);
    }
  };
  const newStudy = () => {
    ++selectionVersion.current;
    ++projectSearchVersion.current;
    setStudy(blankStudy(bootstrap));
    setDirty(false);
    setMessage(null);
    setProjectQuery("");
    setProjectResults([]);
    setPage(0);
    setActiveTab("materials");
  };
  const update = <K extends keyof Study>(key: K, value: Study[K]) => {
    setStudy((current) => (current ? { ...current, [key]: value } : current));
    setDirty(true);
  };
  const changeCompany = (company: string) => {
    const currency =
      bootstrap?.companies.find((item) => item.name === company)?.currency ||
      "USD";
    setStudy((current) =>
      current ? { ...current, company, currency, projects: [] } : current,
    );
    setProjectQuery("");
    setProjectResults([]);
    setDirty(true);
  };
  const updateMaterial = (index: number, key: string, value: unknown) => {
    setStudy((current) =>
      current
        ? {
            ...current,
            materials: current.materials.map((row, rowIndex) =>
              rowIndex === index
                ? { ...row, [key]: value, ...(key === "unit_cost" && Number(value) > 0 ? { cost_known: true } : {}) }
                : row,
            ),
          }
        : current,
    );
    setDirty(true);
  };
  const updateLabor = (index: number, key: string, value: unknown) => {
    setStudy((current) =>
      current
        ? {
            ...current,
            labor: current.labor.map((row, rowIndex) =>
              rowIndex === index
                ? { ...row, [key]: value, ...(key === "hourly_cost" && Number(value) > 0 ? { cost_known: true } : {}) }
                : row,
            ),
          }
        : current,
    );
    setDirty(true);
  };

  useEffect(() => {
    const searchId = ++projectSearchVersion.current;
    if (!study || !isSourceEditable(study.status) || !projectQuery.trim()) {
      setProjectResults([]);
      return;
    }
    const timer = window.setTimeout(() => {
      api
        .searchProjects(study.company, projectQuery)
        .then((results) => {
          if (searchId === projectSearchVersion.current)
            setProjectResults(results);
        })
        .catch((error) => {
          if (searchId === projectSearchVersion.current) reportError(error);
        });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      ++projectSearchVersion.current;
    };
  }, [projectQuery, study?.company, study?.status, reportError]);
  useEffect(() => {
    const name = study?.name;
    if (!name || !study || !isActiveStatus(study.status) || dirty || authError)
      return;
    let cancelled = false;
    let pending = false;
    const timer = window.setInterval(async () => {
      if (pending || dirtyRef.current) return;
      pending = true;
      try {
        const incoming = await api.getStudy(name);
        if (!cancelled && !dirtyRef.current)
          setStudy((current) => (current?.name === name ? incoming : current));
      } catch (error) {
        if (!cancelled) reportError(error);
      } finally {
        pending = false;
      }
    }, 7000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [study?.name, study?.status, dirty, authError, reportError]);

  const save = async () => {
    if (!study) return null;
    if (
      !study.title.trim() ||
      !study.company ||
      !study.standard_description.trim() ||
      !study.projects.length
    ) {
      setMessage({
        kind: "error",
        text: "Add a title, company, configuration, and at least one project before saving.",
      });
      return null;
    }
    if (
      study.projects.some(
        (project) =>
          !Number.isInteger(Number(project.vehicle_count)) ||
          Number(project.vehicle_count) < 1,
      )
    ) {
      setMessage({
        kind: "error",
        text: "Vehicle count must be a positive whole number for every project.",
      });
      return null;
    }
    if (
      [...study.materials, ...study.labor].some(
        (row) => manualRow(row) && !row.notes?.trim(),
      )
    ) {
      setMessage({
        kind: "error",
        text: "Manual rows need a note explaining their source or assumption.",
      });
      return null;
    }
    setSaving(true);
    setMessage(null);
    try {
      const saved = await api.saveStudy(toSavePayload(study));
      setStudy(saved);
      setDirty(false);
      if (await refreshList())
        setMessage({ kind: "success", text: "Saved to the server." });
      return saved;
    } catch (error) {
      reportError(error);
      return null;
    } finally {
      setSaving(false);
    }
  };
  const saveThen = async (action: (saved: Study) => Promise<void>) => {
    const saved = dirty || !study?.name ? await save() : study;
    if (saved) await action(saved);
  };
  const build = () => {
    if (!study || study.status !== "Draft") return;
    return saveThen(async (saved) => {
      setSaving(true);
      try {
        const result = await api.start(saved.name);
        setStudy((current) =>
          current ? { ...current, status: result.status } : current,
        );
        setMessage({ kind: "success", text: "Draft build queued." });
      } catch (error) {
        reportError(error);
      } finally {
        setSaving(false);
      }
    });
  };
  const resume = () =>
    saveThen(async (saved) => {
      setSaving(true);
      try {
        const result = await api.start(saved.name);
        setStudy((current) =>
          current ? { ...current, status: result.status } : current,
        );
        setMessage({ kind: "success", text: "Resume queued." });
      } catch (error) {
        reportError(error);
      } finally {
        setSaving(false);
      }
    });
  const recover = async () => {
    if (!study) return;
    setSaving(true);
    try {
      const result = await api.recover(study.name);
      setStudy((current) =>
        current ? { ...current, status: result.status } : current,
      );
      setMessage({
        kind: "success",
        text: "Interrupted run marked failed. Resume when ready.",
      });
    } catch (error) {
      reportError(error);
    } finally {
      setSaving(false);
    }
  };
  const exportCsv = async () => {
    if (!study || dirty) {
      setMessage({
        kind: "error",
        text: "Save your review edits before exporting a CSV.",
      });
      return;
    }
    try {
      await api.exportCsv(study.name);
      setMessage({ kind: "success", text: "CSV export downloaded." });
    } catch (error) {
      reportError(error);
    }
  };
  const signOut = async () => {
    try {
      await api.logout();
      window.location.href = LOGIN_URL;
    } catch (error) {
      reportError(error);
    }
  };
  const editable = Boolean(
    study && isEditableStatus(study.status) && !saving && !loading,
  );
  const sourceEditable = Boolean(
    study && isSourceEditable(study.status) && !saving && !loading,
  );
  const rows =
    activeTab === "materials" ? study?.materials || [] : study?.labor || [];
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const visibleRows = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  useEffect(() => {
    setPage((current) => Math.min(current, pageCount - 1));
  }, [pageCount]);

  if (authError)
    return (
      <main className="mx-auto flex min-h-screen max-w-lg items-center px-6">
        <Card className="w-full">
          <CardHeader>
            <CardTitle>Sign in required</CardTitle>
            <CardDescription>
              Your session or manufacturing access has expired.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild>
              <a href={LOGIN_URL}>
                Return to sign in <ArrowRight data-icon="inline-end" />
              </a>
            </Button>
          </CardContent>
        </Card>
      </main>
    );
  if (loading && !bootstrap)
    return (
      <main className="min-h-screen bg-muted/30 p-8">
        <div className="mx-auto flex max-w-7xl flex-col gap-6">
          <Skeleton className="h-8 w-52" />
          <Skeleton className="h-16 w-96" />
          <Skeleton className="h-80 w-full" />
        </div>
      </main>
    );

  return (
    <div className="min-h-screen bg-muted/30 text-foreground">
      <div className="mx-auto grid min-h-screen max-w-[1500px] lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="border-b bg-background p-5 lg:border-b-0 lg:border-r">
          <div className="mb-7 flex items-center gap-3">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
              A
            </div>
            <div>
              <div className="font-semibold tracking-tight">Alumicraft</div>
              <div className="text-[10px] font-semibold uppercase tracking-[.16em] text-muted-foreground">
                Cost workspace
              </div>
            </div>
          </div>
          <Button
            className="mb-6 w-full justify-start"
            disabled={saving || loading}
            onClick={() => navigate(newStudy)}
          >
            <Plus data-icon="inline-start" /> New study
          </Button>
          <div className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[.14em] text-muted-foreground">
            Saved studies
          </div>
          <div className="flex gap-1 overflow-x-auto lg:flex-col">
            {studies.length ? (
              studies.map((item) => (
                <button
                  key={item.name}
                  type="button"
                  disabled={saving || loading}
                  onClick={() =>
                    navigate(() => {
                      void chooseStudy(item.name);
                    })
                  }
                  className={cn(
                    "rounded-lg border px-3 py-3 text-left transition hover:bg-muted",
                    study?.name === item.name
                      ? "border-primary/40 bg-muted"
                      : "border-transparent",
                  )}
                >
                  <span className="block truncate text-sm font-semibold">
                    {item.title || item.name}
                  </span>
                  <span className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span>{dateLabel(item.modified)}</span>
                    <Badge variant={statusVariant(item.status)}>
                      {item.status}
                    </Badge>
                  </span>
                </button>
              ))
            ) : (
              <p className="px-2 text-xs leading-5 text-muted-foreground">
                No saved studies yet. Start with a historical scope.
              </p>
            )}
          </div>
        </aside>
        <main className="min-w-0 px-4 py-5 sm:px-8 lg:px-12">
          <header className="flex items-center justify-between gap-4 text-xs text-muted-foreground">
            <span>Vehicle BOM / Studies</span>
            <span className="flex items-center gap-3">
              <span className="font-medium text-foreground">
                {bootstrap?.user || "Signed-in user"}
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={saving || loading}
                onClick={() =>
                  navigate(() => {
                    void signOut();
                  })
                }
              >
                <LogOut data-icon="inline-start" /> Sign out
              </Button>
            </span>
          </header>
          <div className="mb-6 mt-10 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
            <div>
              <h1 className="text-3xl font-semibold tracking-[-.045em]">
                {study
                  ? study.title || "New vehicle BOM study"
                  : "Vehicle BOM studies"}
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                {study
                  ? "Review historical evidence, adjust the estimate, and keep the saved assumptions visible."
                  : "Grounded cost workspaces for manufacturing decisions."}
              </p>
            </div>
            {study && (
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={statusVariant(study.status)}>
                  {study.status}
                </Badge>
                {dirty && <Badge variant="outline">Unsaved changes</Badge>}
              </div>
            )}
          </div>
          {message && (
            <Alert
              className="mb-5"
              variant={message.kind === "error" ? "destructive" : "default"}
            >
              <AlertCircle data-icon="inline-start" />
              <AlertTitle>
                {message.kind === "error" ? "Needs attention" : "Saved"}
              </AlertTitle>
              <AlertDescription>{message.text}</AlertDescription>
            </Alert>
          )}
          {!study ? (
            <Card>
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <Wrench />
                  </EmptyMedia>
                  <EmptyTitle>Build a grounded vehicle cost study</EmptyTitle>
                  <EmptyDescription>
                    Choose historical project evidence, then review the draft
                    before setting a standard retail price.
                  </EmptyDescription>
                </EmptyHeader>
                <EmptyContent>
                  <Button
                    disabled={saving || loading}
                    onClick={() => navigate(newStudy)}
                  >
                    New study <ArrowRight data-icon="inline-end" />
                  </Button>
                </EmptyContent>
              </Empty>
            </Card>
          ) : (
            <div className="flex flex-col gap-4">
              <Collapsible
                key={`${study.name}-${study.status === "Draft"}`}
                defaultOpen={study.status === "Draft"}
                className="flex flex-col gap-4"
              >
                <CollapsibleTrigger asChild>
                  <Button variant="outline" className="justify-between">
                    Configuration and source projects{" "}
                    <ChevronRight data-icon="inline-end" />
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent className="flex flex-col gap-4">
                  <Card>
                    <CardHeader>
                      <CardTitle>Study setup</CardTitle>
                      <CardDescription>
                        Name the estimate and define what counts as comparable
                        evidence.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <FieldGroup className="grid gap-5 sm:grid-cols-2">
                        <Field>
                          <FieldLabel htmlFor="study-title">
                            Study title
                          </FieldLabel>
                          <Input
                            id="study-title"
                            value={study.title}
                            disabled={!sourceEditable}
                            onChange={(event) =>
                              update("title", event.target.value)
                            }
                          />
                        </Field>
                        <Field>
                          <FieldLabel htmlFor="company">Company</FieldLabel>
                          <Select
                            value={study.company}
                            disabled={!sourceEditable}
                            onValueChange={changeCompany}
                          >
                            <SelectTrigger id="company">
                              <SelectValue placeholder="Choose a company" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectGroup>
                                {(bootstrap?.companies || []).map((company) => (
                                  <SelectItem
                                    key={company.name}
                                    value={company.name}
                                  >
                                    {company.name}
                                  </SelectItem>
                                ))}
                              </SelectGroup>
                            </SelectContent>
                          </Select>
                        </Field>
                        <Field>
                          <FieldLabel htmlFor="mode">Mode</FieldLabel>
                          <Select
                            value={study.mode}
                            disabled={!sourceEditable}
                            onValueChange={(value) => update("mode", value)}
                          >
                            <SelectTrigger id="mode">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectGroup>
                                <SelectItem value="Historical only">
                                  Historical only
                                </SelectItem>
                                <SelectItem value="Jev">Jev</SelectItem>
                              </SelectGroup>
                            </SelectContent>
                          </Select>
                        </Field>
                        <Field className="sm:col-span-2">
                          <FieldLabel htmlFor="configuration">
                            Vehicle configuration
                          </FieldLabel>
                          <Textarea
                            id="configuration"
                            value={study.standard_description}
                            disabled={!sourceEditable}
                            onChange={(event) =>
                              update("standard_description", event.target.value)
                            }
                          />
                          <FieldDescription>
                            Describe the standard vehicle configuration the
                            evidence should represent.
                          </FieldDescription>
                        </Field>
                      </FieldGroup>
                      {!sourceEditable && (
                        <Alert className="mt-5">
                          <ShieldCheck data-icon="inline-start" />
                          <AlertDescription>
                            Source configuration is frozen after the study is
                            queued. Review edits unlock when the draft is ready.
                          </AlertDescription>
                        </Alert>
                      )}
                    </CardContent>
                  </Card>
                  <Card>
                    <CardHeader>
                      <CardTitle>Historical scope</CardTitle>
                      <CardDescription>
                        Vehicle counts support per-vehicle comparisons;
                        purchases do not prove installed quantity.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <Field>
                        <FieldLabel htmlFor="project-search">
                          Projects
                        </FieldLabel>
                        <div className="relative">
                          <Input
                            id="project-search"

                            placeholder="Search projects by name"
                            value={projectQuery}
                            disabled={!sourceEditable}
                            onChange={(event) =>
                              setProjectQuery(event.target.value)
                            }
                          />
                          {projectResults.length > 0 && (
                            <div className="mt-2 flex flex-col gap-1 rounded-md border bg-popover p-1">
                              {projectResults.map((project) => (
                                <button
                                  key={project.name}
                                  type="button"
                                  className="flex w-full flex-col rounded-sm px-3 py-2 text-left text-sm hover:bg-muted"
                                  onClick={() => {
                                    if (
                                      !study.projects.some(
                                        (row) => row.project === project.name,
                                      )
                                    )
                                      update("projects", [
                                        ...study.projects,
                                        {
                                          project: project.name,
                                          vehicle_count: 1,
                                        },
                                      ]);
                                    setProjectQuery("");
                                    setProjectResults([]);
                                  }}
                                >
                                  <span className="font-medium">
                                    {project.project_name || project.name}
                                  </span>
                                  <span className="text-xs text-muted-foreground">
                                    {project.status || project.name}
                                  </span>
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      </Field>
                      <div className="mt-3 flex flex-col gap-2">
                        {study.projects.map((project, index) => (
                          <div
                            key={`${project.name || project.project}-${index}`}
                            className="flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center"
                          >
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-sm font-medium">
                                {project.project}
                              </div>
                              <div className="text-xs text-muted-foreground">
                                Project evidence
                              </div>
                            </div>
                            <Input
                              className="w-full sm:w-28"
                              type="number"
                              min="1"
                              step="1"
                              value={project.vehicle_count}
                              disabled={!sourceEditable}
                              aria-label={`Vehicle count for ${project.project}`}
                              onChange={(event) =>
                                update(
                                  "projects",
                                  study.projects.map((row, rowIndex) =>
                                    rowIndex === index
                                      ? {
                                          ...row,
                                          vehicle_count: Number(
                                            event.target.value,
                                          ),
                                        }
                                      : row,
                                  ),
                                )
                              }
                            />
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={!sourceEditable}
                              onClick={() =>
                                update(
                                  "projects",
                                  study.projects.filter(
                                    (_, rowIndex) => rowIndex !== index,
                                  ),
                                )
                              }
                            >
                              Remove
                            </Button>
                          </div>
                        ))}
                      </div>
                      <Separator className="my-5" />
                      <FieldGroup className="grid gap-5 sm:grid-cols-2">
                        <Field>
                          <FieldLabel htmlFor="from-date">From date</FieldLabel>
                          <Input
                            id="from-date"
                            type="date"
                            value={study.from_date || ""}
                            disabled={!sourceEditable}
                            onChange={(event) =>
                              update("from_date", event.target.value)
                            }
                          />
                        </Field>
                        <Field>
                          <FieldLabel htmlFor="to-date">To date</FieldLabel>
                          <Input
                            id="to-date"
                            type="date"
                            value={study.to_date || ""}
                            disabled={!sourceEditable}
                            onChange={(event) =>
                              update("to_date", event.target.value)
                            }
                          />
                        </Field>
                      </FieldGroup>
                    </CardContent>
                  </Card>
                </CollapsibleContent>
              </Collapsible>
              <ReviewCard
                study={study}
                editable={study.status === "Needs Review" && editable}
                dirty={dirty}
                activeTab={activeTab}
                setActiveTab={(tab) => {
                  setActiveTab(tab);
                  setPage(0);
                }}
                page={page}
                setPage={setPage}
                pageCount={pageCount}
                visibleRows={visibleRows}
                setEvidence={setEvidence}
                updateMaterial={updateMaterial}
                updateLabor={updateLabor}
                addManual={(type) => {
                  setPage(Math.floor(study[type].length / PAGE_SIZE));
                  if (type === "materials")
                    update("materials", [
                      ...study.materials,
                      {
                        isManual: true,
                        description: "",
                        stock_uom: "Nos",
                        assembly: "Unknown",
                        proposed_quantity: 1,
                        unit_cost: 0,
                        cost_known: false,
                        purpose: "Unknown",
                        review_status: "Pending",
                        notes: "",
                      },
                    ]);
                  else
                    update("labor", [
                      ...study.labor,
                      {
                        isManual: true,
                        activity_type: "",
                        proposed_hours: 1,
                        hourly_cost: 0,
                        cost_known: false,
                        review_status: "Pending",
                        notes: "",
                      },
                    ]);
                }}
              />
              <EstimateCard
                study={study}
                editable={editable}
                dirty={dirty}
                update={update}
              />
              <footer className="flex flex-wrap justify-end gap-2 py-2">
                <Button
                  variant="outline"
                  disabled={!editable || (!dirty && Boolean(study.name))}
                  onClick={save}
                >
                  {saving ? (
                    <Loader2
                      className="animate-spin"
                      data-icon="inline-start"
                    />
                  ) : (
                    <Check data-icon="inline-start" />
                  )}{" "}
                  Save changes
                </Button>
                {isActiveStatus(study.status) ? (
                  <Button variant="outline" disabled={saving || loading} onClick={recover}>
                    <RefreshCw data-icon="inline-start" /> Recover run
                  </Button>
                ) : study.status === "Failed" ? (
                  <Button disabled={saving || loading} onClick={resume}>
                    Resume <ArrowRight data-icon="inline-end" />
                  </Button>
                ) : study.status === "Draft" ? (
                  <Button disabled={saving || loading} onClick={build}>
                    {saving ? (
                      <Loader2
                        className="animate-spin"
                        data-icon="inline-start"
                      />
                    ) : (
                      <Sparkles data-icon="inline-start" />
                    )}{" "}
                    Build draft
                  </Button>
                ) : null}
                {study.status === "Needs Review" && (
                  <Button
                    variant="outline"
                    disabled={saving || loading || dirty}
                    onClick={exportCsv}
                  >
                    <Download data-icon="inline-start" /> Export CSV
                  </Button>
                )}
              </footer>
            </div>
          )}
        </main>
        <Dialog
          open={navigation !== null}
          onOpenChange={(open) => {
            if (!open) setNavigation(null);
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Discard unsaved changes?</DialogTitle>
              <DialogDescription>
                Your saved study will remain unchanged. Stay here to save your
                edits first.
              </DialogDescription>
            </DialogHeader>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setNavigation(null)}>
                Keep editing
              </Button>
              <Button
                variant="destructive"
                onClick={() => {
                  const action = navigation;
                  setNavigation(null);
                  setDirty(false);
                  action?.();
                }}
              >
                Discard changes
              </Button>
            </div>
          </DialogContent>
        </Dialog>
        <Dialog
          open={evidence !== null}
          onOpenChange={(open) => !open && setEvidence(null)}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Evidence detail</DialogTitle>
              <DialogDescription>
                Read-only source evidence returned with this study.
              </DialogDescription>
            </DialogHeader>
            <pre className="max-h-[60vh] overflow-auto rounded-md bg-muted p-4 text-xs leading-5 whitespace-pre-wrap">
              {typeof evidence === "string"
                ? evidence
                : JSON.stringify(evidence, null, 2)}
            </pre>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}

function ReviewCard({
  study,
  editable,
  dirty,
  activeTab,
  setActiveTab,
  page,
  setPage,
  pageCount,
  visibleRows,
  setEvidence,
  updateMaterial,
  updateLabor,
  addManual,
}: {
  study: Study;
  editable: boolean;
  dirty: boolean;
  activeTab: "materials" | "labor";
  setActiveTab: (tab: "materials" | "labor") => void;
  page: number;
  setPage: (page: number) => void;
  pageCount: number;
  visibleRows: Array<MaterialRow | LaborRow>;
  setEvidence: (value: unknown) => void;
  updateMaterial: (index: number, key: string, value: unknown) => void;
  updateLabor: (index: number, key: string, value: unknown) => void;
  addManual: (type: "materials" | "labor") => void;
}) {
  const offset = page * PAGE_SIZE;
  return (
    <Card>
      <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <CardTitle>Review worksheet</CardTitle>
          <CardDescription>
            {editable
              ? "Adjust proposed quantities, costs, and review decisions. Source evidence stays read-only."
              : "Build the draft to review components and labor."}
          </CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={!editable}
          onClick={() => addManual(activeTab)}
        >
          <Plus data-icon="inline-start" /> Add manual{" "}
          {activeTab === "materials" ? "material" : "labor"}
        </Button>
      </CardHeader>
      <CardContent>
        <Tabs
          value={activeTab}
          onValueChange={(value) =>
            setActiveTab(value as "materials" | "labor")
          }
        >
          <TabsList>
            <TabsTrigger value="materials">
              Materials{" "}
              <span className="ml-1 text-muted-foreground">
                {study.materials.length}
              </span>
            </TabsTrigger>
            <TabsTrigger value="labor">
              Labor{" "}
              <span className="ml-1 text-muted-foreground">
                {study.labor.length}
              </span>
            </TabsTrigger>
          </TabsList>
          <TabsContent value="materials" className="mt-4">
            <ReviewTable
              type="materials"
              rows={visibleRows as MaterialRow[]}
              editable={editable}
              offset={offset}
              currency={study.currency || "USD"}
              dirty={dirty}
              setEvidence={setEvidence}
              update={updateMaterial}
            />
          </TabsContent>
          <TabsContent value="labor" className="mt-4">
            <ReviewTable
              type="labor"
              rows={visibleRows as LaborRow[]}
              editable={editable}
              offset={offset}
              currency={study.currency || "USD"}
              dirty={dirty}
              setEvidence={setEvidence}
              update={updateLabor}
            />
          </TabsContent>
        </Tabs>
        {pageCount > 1 && (
          <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
            <span>
              Rows {offset + 1}–
              {Math.min(
                offset + PAGE_SIZE,
                activeTab === "materials"
                  ? study.materials.length
                  : study.labor.length,
              )}{" "}
              of{" "}
              {activeTab === "materials"
                ? study.materials.length
                : study.labor.length}
            </span>
            <div className="flex gap-1">
              <Button
                variant="outline"
                size="icon"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
                aria-label="Previous page"
              >
                <ChevronLeft />
              </Button>
              <span className="flex min-w-16 items-center justify-center">
                Page {page + 1} of {pageCount}
              </span>
              <Button
                variant="outline"
                size="icon"
                disabled={page >= pageCount - 1}
                onClick={() => setPage(page + 1)}
                aria-label="Next page"
              >
                <ChevronRight />
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ReviewTable({
  type,
  rows,
  editable,
  offset,
  currency,
  dirty,
  setEvidence,
  update,
}: {
  type: "materials" | "labor";
  rows: Array<MaterialRow | LaborRow>;
  editable: boolean;
  offset: number;
  currency: string;
  dirty: boolean;
  setEvidence: (value: unknown) => void;
  update: (index: number, key: string, value: unknown) => void;
}) {
  const material = type === "materials";
  const headers = material
    ? [
        "Component",
        "Assembly",
        "Quantity",
        "Unit",
        "Unit cost",
        "Cost known",
        "Review",
        "Purpose",
        "Notes",
        "Saved amount",
      ]
    : [
        "Activity",
        "Hours",
        "Hourly cost",
        "Cost known",
        "Review",
        "Notes",
        "Saved amount",
      ];
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {headers.map((label) => (
            <TableHead key={label}>{label}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.length ? (
          rows.map((row, index) => {
            const position = offset + index;
            const label = `${material ? "Component" : "Labor"} ${position + 1}`;
            const m = row as MaterialRow;
            const l = row as LaborRow;
            const input = (
              key: string,
              value: string | number,
              name: string,
              numeric = false,
              width = "w-28",
            ) => (
              <Input
                className={width}
                aria-label={`${name} for ${label}`}
                disabled={!editable}
                value={value}
                type={numeric ? "number" : "text"}
                min={numeric ? 0 : undefined}
                step={numeric ? "any" : undefined}
                onChange={(event) =>
                  update(
                    position,
                    key,
                    numeric ? Number(event.target.value) : event.target.value,
                  )
                }
              />
            );
            const choices = (
              key: string,
              value: string,
              options: string[],
              name: string,
            ) => (
              <Select
                disabled={!editable}
                value={value}
                onValueChange={(value) => update(position, key, value)}
              >
                <SelectTrigger
                  aria-label={`${name} for ${label}`}
                  className="min-w-32"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {options.map((value) => (
                      <SelectItem key={value} value={value}>
                        {value}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            );
            return (
              <TableRow key={row.name || `${type}-${position}`}>
                <TableCell>
                  <div className="flex min-w-56 flex-col gap-2">
                    {input(
                      material ? "description" : "activity_type",
                      (material ? m.description : l.activity_type) || "",
                      material ? "Description" : "Activity",
                      false,
                      "min-w-56",
                    )}
                    {material && m.item_code ? (
                      <span className="text-xs text-muted-foreground">
                        {m.item_code}
                      </span>
                    ) : null}
                    {row.evidence ? (
                      <Button
                        variant="link"
                        size="sm"
                        onClick={() => setEvidence(row.evidence)}
                      >
                        View evidence
                      </Button>
                    ) : null}
                  </div>
                </TableCell>
                {material ? (
                  <TableCell>
                    {choices(
                      "assembly",
                      m.assembly || "Unknown",
                      ASSEMBLIES,
                      "Assembly",
                    )}
                  </TableCell>
                ) : null}
                <TableCell>
                  {input(
                    material ? "proposed_quantity" : "proposed_hours",
                    Number(material ? m.proposed_quantity : l.proposed_hours) ||
                      0,
                    material ? "Quantity" : "Hours",
                    true,
                  )}
                </TableCell>
                {material ? (
                  <TableCell>
                    {input("stock_uom", m.stock_uom || "", "Unit of measure")}
                  </TableCell>
                ) : null}
                <TableCell>
                  {input(
                    material ? "unit_cost" : "hourly_cost",
                    Number(material ? m.unit_cost : l.hourly_cost) || 0,
                    material ? "Unit cost" : "Hourly cost",
                    true,
                  )}
                </TableCell>
                <TableCell>
                  <Checkbox
                    aria-label={`Cost known for ${label}`}
                    checked={Boolean(row.cost_known)}
                    disabled={!editable}
                    onCheckedChange={(value) =>
                      update(position, "cost_known", value === true)
                    }
                  />
                </TableCell>
                <TableCell>
                  {choices(
                    "review_status",
                    row.review_status || "Pending",
                    REVIEWS,
                    "Review status",
                  )}
                </TableCell>
                {material ? (
                  <TableCell>
                    {choices(
                      "purpose",
                      m.purpose || "Unknown",
                      PURPOSES,
                      "Purpose",
                    )}
                  </TableCell>
                ) : null}
                <TableCell>
                  {input("notes", row.notes || "", "Notes", false, "min-w-56")}
                </TableCell>
                <TableCell>
                  {dirty ? "Save to refresh" : money(row.amount, currency)}
                </TableCell>
              </TableRow>
            );
          })
        ) : (
          <TableRow>
            <TableCell colSpan={headers.length}>
              <Empty>
                <EmptyHeader>
                  <EmptyTitle>
                    No {material ? "components" : "labor entries"} yet
                  </EmptyTitle>
                  <EmptyDescription>
                    Build a draft from the selected projects to begin reviewing
                    evidence.
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

function EstimateCard({
  study,
  editable,
  dirty,
  update,
}: {
  study: Study;
  editable: boolean;
  dirty: boolean;
  update: <K extends keyof Study>(key: K, value: Study[K]) => void;
}) {
  const metric = (
    label: string,
    value: number | null | undefined,
    note: string,
  ) => (
    <div className="rounded-lg border bg-background p-4">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-2 text-xl font-semibold tracking-tight">
        {dirty || (label === "Suggested retail" && !value)
          ? "—"
          : money(value, study.currency || "USD")}
      </div>
      <div className="mt-1 text-[11px] text-muted-foreground">
        {dirty ? "Save to refresh" : note}
      </div>
    </div>
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cost summary</CardTitle>
        <CardDescription>
          {dirty
            ? "Totals are hidden until these edits are saved to the server."
            : "Server-saved totals from the latest draft."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {metric("Materials", study.material_total, "Saved material total")}
          {metric("Labor", study.labor_total, "Saved labor total")}
          {metric("Estimated cost", study.estimated_total, "Saved estimate")}
          {metric(
            "Suggested retail",
            study.suggested_retail,
            study.suggested_retail
              ? "At selected margin"
              : "Unavailable when evidence is incomplete",
          )}
        </div>
        <Separator className="my-5" />
        <FieldGroup className="grid gap-5 sm:grid-cols-3">
          <Field>
            <FieldLabel htmlFor="material-allowance">
              Material allowance
            </FieldLabel>
            <Input
              id="material-allowance"
              type="number"
              step=".01"
              disabled={!editable}
              value={study.material_allowance}
              onChange={(event) =>
                update("material_allowance", Number(event.target.value) || 0)
              }
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="overhead-allowance">
              Overhead allowance
            </FieldLabel>
            <Input
              id="overhead-allowance"
              type="number"
              step=".01"
              disabled={!editable}
              value={study.overhead_allowance}
              onChange={(event) =>
                update("overhead_allowance", Number(event.target.value) || 0)
              }
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="target-margin">Target margin %</FieldLabel>
            <Input
              id="target-margin"
              type="number"
              min="0"
              max="100"
              step=".1"
              disabled={!editable}
              value={study.target_margin}
              onChange={(event) =>
                update("target_margin", Number(event.target.value) || 0)
              }
            />
          </Field>
        </FieldGroup>
        {study.warnings && (
          <Alert className="mt-5">
            <AlertCircle data-icon="inline-start" />
            <AlertDescription>{study.warnings}</AlertDescription>
          </Alert>
        )}
        {study.error_message && (
          <Alert variant="destructive" className="mt-5">
            <AlertCircle data-icon="inline-start" />
            <AlertDescription>{study.error_message}</AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}
