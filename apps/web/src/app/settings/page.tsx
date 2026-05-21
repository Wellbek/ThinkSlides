"use client";

export default function SettingsPage() {
  return (
    <div className="min-h-screen bg-surface p-8">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-2xl font-bold text-gray-100 mb-2">Settings</h1>
        <p className="text-sm text-gray-500 mb-8">
          Manage your organisation settings and preferences
        </p>

        <div className="space-y-6">
          {/* Organisation */}
          <section className="bg-surface-50 border border-surface-100 rounded-xl p-6">
            <h2 className="text-lg font-medium text-gray-200 mb-4">
              Organisation
            </h2>
            <p className="text-sm text-gray-400">
              Organisation settings will appear here. Navigate to the{" "}
              <a href="/templates" className="text-brand-400 hover:underline">
                Templates
              </a>{" "}
              page to manage your presentation templates.
            </p>
          </section>

          {/* API Keys */}
          <section className="bg-surface-50 border border-surface-100 rounded-xl p-6">
            <h2 className="text-lg font-medium text-gray-200 mb-4">
              LLM Provider
            </h2>
            <p className="text-sm text-gray-400">
              The LLM provider is configured server-side via environment
              variables. Contact your administrator to change between Zhipu GLM
              and Azure OpenAI.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}