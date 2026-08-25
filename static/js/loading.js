// Universal loading overlay
const loaderHTML = `
<div id="loading-overlay" class="fixed inset-0 bg-black/80 flex flex-col items-center justify-center z-50 hidden">
  <div class="w-64 h-1 bg-gray-700 rounded-full overflow-hidden mb-6">
    <div id="loading-bar" class="h-full bg-cyan-400 w-0 transition-all duration-300"></div>
  </div>
  <p id="loading-text" class="text-cyan-300 font-medium">Processing...</p>
</div>`;
document.body.insertAdjacentHTML("beforeend", loaderHTML);

let loaderTimer;
function showLoading(text="Processing...") {
  const overlay = document.getElementById("loading-overlay");
  const bar = document.getElementById("loading-bar");
  const txt = document.getElementById("loading-text");
  txt.textContent = text;
  overlay.classList.remove("hidden");
  bar.style.width = "0%";
  let w = 0;
  loaderTimer = setInterval(()=>{ if(w<90){w+=5;bar.style.width=w+"%";}},200);
}
function updateLoadingText(text){document.getElementById("loading-text").textContent=text;}
function hideLoading() {
  clearInterval(loaderTimer);
  const overlay = document.getElementById("loading-overlay");
  const bar = document.getElementById("loading-bar");

  if (!overlay || !bar) {
    console.warn("hideLoading() called but overlay not found");
    return;
  }

  bar.style.width = "100%";
  setTimeout(() => {
    overlay.classList.add("hidden");
    bar.style.width = "0%";
  }, 400);
}
