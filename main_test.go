package main

import (
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

const testOrigin = "http://127.0.0.1:8000"
const testAllowedHosts = "127.0.0.1,localhost,testserver"

func publicRequest(method, target string, body io.Reader) *http.Request {
	request := httptest.NewRequest(method, target, body)
	request.Host = "127.0.0.1:8000"
	return request
}

func TestAPIIndexDocumentsUnifiedEndpoints(t *testing.T) {
	workerURL, _ := url.Parse("http://127.0.0.1:8001")
	response := httptest.NewRecorder()
	newAPIServer(workerURL, testOrigin, testAllowedHosts).handler().ServeHTTP(response, publicRequest(http.MethodGet, "/api", nil))

	if response.Code != http.StatusOK {
		t.Fatalf("GET /api returned %d", response.Code)
	}
	for _, expected := range []string{
		"/api/v1/engine",
		"/api/v1/engine/compose",
		"/api/v1/engine/transform",
		"/api/v1/engine/protect",
		"/api/v1/engine/inspect",
		"/api/v1/engine/pipeline",
	} {
		if !strings.Contains(response.Body.String(), expected) {
			t.Errorf("API index does not include %q", expected)
		}
	}
	if strings.Contains(response.Body.String(), "/api/tools") || strings.Contains(response.Body.String(), "/api/process") {
		t.Error("API index still advertises catalog or tool-specific routes")
	}
}

func TestRemovedOCRLanguagesEndpointIsNotAvailable(t *testing.T) {
	workerURL, _ := url.Parse("http://127.0.0.1:8001")
	response := httptest.NewRecorder()
	newAPIServer(workerURL, testOrigin, testAllowedHosts).handler().ServeHTTP(
		response,
		publicRequest(http.MethodGet, "/api/ocr-languages", nil),
	)
	if response.Code != http.StatusNotFound {
		t.Fatalf("GET /api/ocr-languages returned %d, want 404", response.Code)
	}
}

func TestGoAPIProcessesMultipartThroughPrivateWorker(t *testing.T) {
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/engine/compose" {
			t.Errorf("worker received path %q", r.URL.Path)
		}
		if r.Host == "" {
			t.Error("worker received an empty Host header")
		}
		if got := r.Header.Get("Origin"); got != testOrigin {
			t.Errorf("worker received Origin %q", got)
		}
		if err := r.ParseMultipartForm(1 << 20); err != nil {
			t.Errorf("worker received invalid multipart request: %v", err)
			return
		}
		if got := r.FormValue("steps"); got != `[{"primitive":"compose","operation":"rotate","options":{"rotation":180}}]` {
			t.Errorf("worker received steps %q", got)
		}
		files := r.MultipartForm.File["files"]
		if len(files) != 1 {
			t.Errorf("worker received %d files, want one", len(files))
			return
		}
		file, err := files[0].Open()
		if err != nil {
			t.Errorf("open worker file: %v", err)
			return
		}
		defer file.Close()
		body, err := io.ReadAll(file)
		if err != nil {
			t.Errorf("read worker file: %v", err)
		}
		if string(body) != "input-pdf" {
			t.Errorf("worker received unexpected file content %q", body)
		}
		w.Header().Set("Content-Type", "application/pdf")
		w.Header().Set("Content-Disposition", `attachment; filename="merged.pdf"`)
		w.Header().Set("X-Input-Bytes", "9")
		w.Header().Set("X-Output-Bytes", "12")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("generated-pdf"))
	}))
	defer worker.Close()
	workerURL, err := url.Parse(worker.URL)
	if err != nil {
		t.Fatal(err)
	}

	var body strings.Builder
	form := multipart.NewWriter(&body)
	if err := form.WriteField("steps", `[{"primitive":"compose","operation":"rotate","options":{"rotation":180}}]`); err != nil {
		t.Fatal(err)
	}
	filePart, err := form.CreateFormFile("files", "source.pdf")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := filePart.Write([]byte("input-pdf")); err != nil {
		t.Fatal(err)
	}
	if err := form.Close(); err != nil {
		t.Fatal(err)
	}
	request := publicRequest(http.MethodPost, "/api/v1/engine/compose", strings.NewReader(body.String()))
	request.Header.Set("Content-Type", form.FormDataContentType())
	request.Header.Set("Origin", testOrigin)
	response := httptest.NewRecorder()
	newAPIServer(workerURL, testOrigin, testAllowedHosts).handler().ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("API returned %d: %s", response.Code, response.Body.String())
	}
	if got := response.Header().Get("Content-Disposition"); got != `attachment; filename="merged.pdf"` {
		t.Errorf("unexpected Content-Disposition %q", got)
	}
	if got := response.Header().Get("X-Output-Bytes"); got != "12" {
		t.Errorf("unexpected X-Output-Bytes %q", got)
	}
	if response.Body.String() != "generated-pdf" {
		t.Errorf("unexpected response body %q", response.Body.String())
	}
}

func TestEngineCapabilitiesEndpointUsesGoAPIContract(t *testing.T) {
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/engine" {
			t.Errorf("worker received path %q", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"name":"Prism Composable Document Engine","primitives":{"compose":{"rotate":[]}}}`))
	}))
	defer worker.Close()
	workerURL, _ := url.Parse(worker.URL)

	response := httptest.NewRecorder()
	newAPIServer(workerURL, testOrigin, testAllowedHosts).handler().ServeHTTP(response, publicRequest(http.MethodGet, "/api/v1/engine", nil))

	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"name":"Prism Composable Document Engine"`) {
		t.Fatalf("unexpected capabilities response %d: %s", response.Code, response.Body.String())
	}
}

func TestHealthReportsWorkerAvailability(t *testing.T) {
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
	}))
	defer worker.Close()
	workerURL, _ := url.Parse(worker.URL)

	response := httptest.NewRecorder()
	newAPIServer(workerURL, testOrigin, testAllowedHosts).handler().ServeHTTP(response, publicRequest(http.MethodGet, "/health", nil))
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("health returned %d, want 503", response.Code)
	}
	if !strings.Contains(response.Body.String(), `"status":"unavailable"`) {
		t.Errorf("health response was not JSON: %s", response.Body.String())
	}
}

func TestGoAPIRejectsOversizedRequest(t *testing.T) {
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("oversized request reached the worker")
	}))
	defer worker.Close()
	workerURL, _ := url.Parse(worker.URL)

	request := publicRequest(http.MethodPost, "/api/v1/engine/compose", strings.NewReader("large"))
	request.ContentLength = maxRequestBytes + 1
	request.Header.Set("Origin", testOrigin)
	response := httptest.NewRecorder()
	newAPIServer(workerURL, testOrigin, testAllowedHosts).handler().ServeHTTP(response, request)

	if response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("API returned %d, want 413", response.Code)
	}
	if !strings.Contains(response.Body.String(), `"detail"`) {
		t.Errorf("oversize response was not JSON: %s", response.Body.String())
	}
}

func TestGoAPIRequiresAllowedOriginForMutation(t *testing.T) {
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("disallowed request reached the worker")
	}))
	defer worker.Close()
	workerURL, _ := url.Parse(worker.URL)

	request := publicRequest(http.MethodPost, "/api/v1/engine/compose", strings.NewReader(""))
	response := httptest.NewRecorder()
	newAPIServer(workerURL, testOrigin, testAllowedHosts).handler().ServeHTTP(response, request)

	if response.Code != http.StatusForbidden {
		t.Fatalf("API returned %d, want 403", response.Code)
	}
	if !strings.Contains(response.Body.String(), `"detail"`) {
		t.Errorf("origin error was not JSON: %s", response.Body.String())
	}
}

func TestGoAPIRejectsUnconfiguredHost(t *testing.T) {
	workerURL, _ := url.Parse("http://127.0.0.1:8001")
	request := httptest.NewRequest(http.MethodGet, "/api", nil)
	request.Host = "untrusted.example"
	response := httptest.NewRecorder()
	newAPIServer(workerURL, testOrigin, testAllowedHosts).handler().ServeHTTP(response, request)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("API returned %d, want 400", response.Code)
	}
	if !strings.Contains(response.Body.String(), `"detail"`) {
		t.Errorf("host error was not JSON: %s", response.Body.String())
	}
}

func TestDefaultAllowedOrigins(t *testing.T) {
	tests := []struct {
		host string
		want string
	}{
		{"127.0.0.1", "http://127.0.0.1:8000"},
		{"0.0.0.0", "http://127.0.0.1:8000,http://localhost:8000"},
	}
	for _, test := range tests {
		t.Run(test.host, func(t *testing.T) {
			if got := defaultAllowedOrigins(test.host, defaultPort); got != test.want {
				t.Errorf("defaultAllowedOrigins() = %s, want %s", got, test.want)
			}
		})
	}
}

func TestValidateAllowedOrigins(t *testing.T) {
	for _, test := range []struct {
		value string
		valid bool
	}{
		{"https://pdf.example.com", true},
		{"http://127.0.0.1:8000,http://localhost:8000", true},
		{"", false},
		{"https://pdf.example.com/path", false},
		{"ftp://pdf.example.com", false},
	} {
		err := validateAllowedOrigins(test.value)
		if (err == nil) != test.valid {
			t.Errorf("validateAllowedOrigins(%q) error = %v, valid = %t", test.value, err, test.valid)
		}
	}
}
