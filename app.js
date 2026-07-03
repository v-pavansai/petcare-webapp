// ==========================================
// 0. GLOBAL APP CONFIG & ALERTS
// ==========================================
const API_URL = "";

// ── SECURITY HELPERS ──────────────────────────────────────────────────────────

/** Escape user-controlled strings before injecting into innerHTML. */
function escapeHTML(str) {
    if (!str) return '';
    const p = document.createElement('p');
    p.textContent = str;
    return p.innerHTML;
}

/** Return headers that include the JWT bearer token for all authenticated calls. */
function getAuthHeaders(extraHeaders = {}) {
    const token = localStorage.getItem('pawcare_auth_token');
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        ...extraHeaders
    };
}

/**
 * If the server responds 401 (token expired / invalid), clear local state
 * and redirect to login.  Returns true if the caller should abort.
 */
function handleAuthError(response) {
    if (response.status === 401 || response.status === 403) {
        localStorage.removeItem('pawcare_auth_token');
        localStorage.removeItem('pawcare_user_email');
        localStorage.removeItem('pawcare_user_name');
        window.location.href = 'login.html';
        return true;
    }
    return false;
}
// ─────────────────────────────────────────────────────────────────────────────

function showAppAlert(message, type = 'error') {
    const modal = document.getElementById('app-alert-modal');
    const msgEl = document.getElementById('app-alert-message');
    const iconEl = document.getElementById('app-alert-icon');
    const card = document.getElementById('app-alert-card');
    const btn = document.getElementById('app-alert-btn');
    
    msgEl.innerText = message;
    
    // Hide the icon completely for a minimal look
    iconEl.style.display = 'none'; 
    
    // Make the card border thin and subtle instead of thick and colored
    card.style.border = '1px solid var(--border)'; 
    btn.style.border = 'none';
    
    // Apply muted, less vibrant colors just to the button
    if (type === 'error') {
        btn.style.background = '#d66d6d'; // Muted soft red
        btn.style.color = '#ffffff';
    } else if (type === 'success') {
        btn.style.background = '#659c7a'; // Muted soft green
        btn.style.color = '#ffffff';
    } else {
        btn.style.background = '#6b829e'; // Muted soft blue
        btn.style.color = '#ffffff';
    }
    
    modal.style.display = 'flex';
}

function closeAppAlert() {
    document.getElementById('app-alert-modal').style.display = 'none';
}

let pendingConfirmCallback = null;

function showAppConfirm(message, callback) {
    document.getElementById('app-confirm-message').innerText = message;
    pendingConfirmCallback = callback;
    document.getElementById('app-confirm-modal').style.display = 'flex';
}

function closeAppConfirm() {
    document.getElementById('app-confirm-modal').style.display = 'none';
    pendingConfirmCallback = null;
}

function executeAppConfirm() {
    if (pendingConfirmCallback) pendingConfirmCallback();
    closeAppConfirm();
}

// ==========================================
// 1. INITIALIZATION & AUTHENTICATION
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
  const savedEmail = localStorage.getItem('pawcare_user_email');
  const savedName = localStorage.getItem('pawcare_user_name');

  if (!savedEmail) {
      window.location.replace("login.html");
      return;
  }

  if (savedName && savedEmail) {
      document.getElementById('profile-name').innerText = savedName;
      document.getElementById('profile-email').innerText = savedEmail;
      document.getElementById('profile-initial').innerText = savedName.charAt(0).toUpperCase();
  }

  const savedTheme = localStorage.getItem('pawcare_theme');
  const appContainer = document.getElementById('app');
  const themeIcon = document.getElementById('theme-icon');
  if (savedTheme === 'dark') {
    appContainer.setAttribute('data-theme', 'dark');
    if (themeIcon) themeIcon.className = 'ti ti-sun';
  }

  loadUserPets(); 
});

function toggleTheme() {
  const appContainer = document.getElementById('app');
  const themeIcon = document.getElementById('theme-icon');
  if (appContainer.getAttribute('data-theme') === 'dark') {
    appContainer.removeAttribute('data-theme');
    if (themeIcon) themeIcon.className = 'ti ti-moon';
    localStorage.setItem('pawcare_theme', 'light');
  } else {
    appContainer.setAttribute('data-theme', 'dark');
    if (themeIcon) themeIcon.className = 'ti ti-sun';
    localStorage.setItem('pawcare_theme', 'dark');
  }
}

function logout() {
    localStorage.removeItem('pawcare_user_name');
    localStorage.removeItem('pawcare_user_email');
    localStorage.removeItem('pawcare_auth_token');
    window.location.href = "login.html";
}

function navTo(screenId, navElement = null) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(screenId).classList.add('active');
  
  if (navElement) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    navElement.classList.add('active');
  }

  if (screenId === 'vets') {
      setTimeout(initMap, 100); 
  }
}

// ==========================================
// 2. PET MANAGEMENT
// ==========================================

// Extracted from provided Excel files: mapped by Type -> Breed -> Max Age
const petData = {
    "Dog": {
        "Indie (Local)": 15, "Labrador Retriever": 14, "Golden Retriever": 13,
        "German Shepherd": 13, "Pomeranian": 16, "Pug": 15, "Shih Tzu": 18,
        "Beagle": 16, "Rottweiler": 10, "Doberman": 13, "Other": 15
    },
    "Cat": {
        "Indie (Local/Mixed)": 20, "Persian": 17, "Siamese": 20, "Himalayan": 15,
        "Maine Coon": 15, "Bengal": 16, "Other": 18
    },
    "Bird": {
        "Budgie": 10, "Love Bird": 12, "Indian Ringneck": 25, "Cockatiel": 20,
        "Mynah": 25, "Green Indian Parrot (Tota/Mithoo)": 25, "Finch": 10, "Other": 15
    },
    "Fish": {
        "Goldfish": 30, "Betta (Fighter Fish)": 5, "Guppy": 3, "Tetra": 8,
        "Koi / Carp": 40, "Molly": 5, "Angel Fish": 12, "Other": 8
    },
    "Rabbit": {
        "Local/Mixed": 9, "New Zealand White": 6, "Angora": 12, "Other": 9
    },
    "Turtle": {
        "Red-Eared Slider": 30, "Indian Tent Turtle": 20, "Other": 25
    }
};

function handleBreedChange(selectId, customInputId) {
    const breedSelect = document.getElementById(selectId);
    const customInput = document.getElementById(customInputId);
    
    if (breedSelect.value === 'Other') {
        customInput.style.display = 'block';
    } else {
        customInput.style.display = 'none';
        customInput.value = '';
    }
}

function updateBreeds() {
    const typeSelect = document.getElementById('pet-type').value;
    const breedSelect = document.getElementById('pet-breed');
    const customInput = document.getElementById('custom-pet-breed');
    
    breedSelect.innerHTML = '<option value="">Select Breed...</option>';
    customInput.style.display = 'none';
    customInput.value = '';
    
    if (typeSelect && petData[typeSelect]) {
        breedSelect.disabled = false;
        Object.keys(petData[typeSelect]).forEach(breed => {
            const option = document.createElement('option');
            option.value = breed; option.innerText = breed;
            breedSelect.appendChild(option);
        });
    } else { 
        breedSelect.disabled = true; 
    }
}

function updateBreedsForEdit() {
    const typeSelect = document.getElementById('update-pet-type').value;
    const breedSelect = document.getElementById('update-pet-breed');
    const customInput = document.getElementById('custom-update-pet-breed');
    
    breedSelect.innerHTML = '<option value="">Select Breed...</option>';
    customInput.style.display = 'none';
    customInput.value = '';
    
    if (typeSelect && petData[typeSelect]) {
        breedSelect.disabled = false;
        Object.keys(petData[typeSelect]).forEach(breed => {
            const option = document.createElement('option');
            option.value = breed; option.innerText = breed;
            breedSelect.appendChild(option);
        });
    } else { 
        breedSelect.disabled = true; 
    }
}

async function loadUserPets() {
    const userEmail = localStorage.getItem('pawcare_user_email');
    const petContainer = document.getElementById('dynamic-pet-list');
    const dietDropdown = document.getElementById('diet-pet-select');
    const detectDropdown = document.getElementById('detect-pet-select');
    
    petContainer.innerHTML = ''; 
    dietDropdown.innerHTML = '<option value="">Select Pet</option>';
    if(detectDropdown) detectDropdown.innerHTML = '<option value="">Select a pet first...</option>';

    try {
        const response = await fetch(`${API_URL}/api/pets/${userEmail}`, { headers: getAuthHeaders() });
        if (handleAuthError(response)) return;
        const pets = await response.json();
        window.currentPets = pets;

        if (pets.length === 0) {
            petContainer.innerHTML = '<p style="color: var(--text-2); font-size: 14px; text-align: center; margin: 20px 0;">No pets added yet.</p>';
            initVaxTab(); 
            return;
        }

        pets.forEach(pet => {
            let icon = 'ti-paw'; let bgColor = 'var(--blue-light)'; let iconColor = 'var(--blue)';
            if (pet.pet_type === 'Dog') { icon = 'ti-dog'; } 
            else if (pet.pet_type === 'Cat') { icon = 'ti-cat'; bgColor = 'var(--amber-light)'; iconColor = 'var(--amber-dark)'; } 
            else if (pet.pet_type === 'Bird') { icon = 'ti-feather'; bgColor = '#dcfce7'; iconColor = '#15803d'; } 
            else if (pet.pet_type === 'Fish') { icon = 'ti-fish'; bgColor = '#e0f2fe'; iconColor = '#0369a1'; } 
            else if (pet.pet_type === 'Rabbit') { icon = 'ti-carrot'; bgColor = '#fce7f3'; iconColor = '#be185d'; } 
            else if (pet.pet_type === 'Turtle') { icon = 'ti-ripple'; bgColor = '#ecfccb'; iconColor = '#4d7c0f'; }

            const safePetName = escapeHTML(pet.name).replace(/'/g, "\\'").replace(/"/g, "&quot;");
            const petHTML = `
              <div class="pet-item" onclick="openPetModal(${parseInt(pet.id)}, '${safePetName}')">
                <div class="pet-info">
                  <div class="pet-icon" style="background: ${bgColor}; color: ${iconColor};"><i class="ti ${icon}"></i></div>
                  <div>
                    <div class="pet-name">${escapeHTML(pet.name)} <span style="font-size: 12px; font-weight: normal; color: var(--text-3);">(${escapeHTML(pet.age)})</span></div>
                    <div class="pet-type">${escapeHTML(pet.breed)}</div>
                  </div>
                </div>
                <i class="ti ti-chevron-right" style="color: var(--text-3);"></i>
              </div>
            `;
            petContainer.innerHTML += petHTML;
            dietDropdown.innerHTML += `<option value="${parseInt(pet.id)}">${escapeHTML(pet.name)} (${escapeHTML(pet.pet_type)})</option>`;
            
            if(detectDropdown) detectDropdown.innerHTML += `<option value="${parseInt(pet.id)}">${escapeHTML(pet.name)} - ${escapeHTML(pet.age)} old ${escapeHTML(pet.breed)}</option>`;
        });

        initVaxTab();
        loadVaccines(); 

    } catch (error) {
        console.error("Error loading pets:", error);
    }
}

async function savePet() {
    const email = localStorage.getItem('pawcare_user_email');
    const type = document.getElementById('pet-type').value;
    let breed = document.getElementById('pet-breed').value;
    const name = document.getElementById('pet-name').value;
    const age = document.getElementById('pet-age').value;

    if (breed === 'Other') {
        breed = document.getElementById('custom-pet-breed').value.trim() || 'Other';
    }

    if (!type || !breed || !name.trim() || !age.trim()) { 
        showAppAlert("Please fill out all fields with valid text!", "error"); 
        return; 
    }
    
    // Strict Age check based on Excel data
    const ageNum = parseFloat(age);
    let maxAge = 60; // Absolute fallback
    
    // Check if the exact breed exists in our mapping, otherwise fallback to "Other" for that pet type
    const lookupBreed = petData[type][breed] ? breed : "Other";
    if (petData[type] && petData[type][lookupBreed]) {
        maxAge = petData[type][lookupBreed];
    }

    if (isNaN(ageNum) || ageNum < 0 || ageNum > maxAge) {
        showAppAlert(`Please enter a realistic age. The maximum age for this breed is approx ${maxAge} years.`, "error"); 
        return;
    }
    
    if (!isNaN(name)) {
        showAppAlert("Pet name cannot be just numbers.", "error");
        return;
    }

    try {
        const response = await fetch(`${API_URL}/api/pets`, {
            method: 'POST', headers: getAuthHeaders(),
            body: JSON.stringify({ owner_email: email, pet_type: type, breed: breed, name: name, age: age })
        });
        if (handleAuthError(response)) return;
        if (response.ok) {
            document.getElementById('add-pet-form').style.display = 'none';
            document.getElementById('pet-name').value = ''; document.getElementById('pet-age').value = ''; document.getElementById('pet-type').value = '';
            document.getElementById('custom-pet-breed').value = '';
            document.getElementById('custom-pet-breed').style.display = 'none';
            updateBreeds(); 
            loadUserPets(); 
            showAppAlert("Pet added successfully!", "success");
        } else { showAppAlert("Failed to save pet.", "error"); }
    } catch (error) { showAppAlert("Cannot connect to server.", "error"); }
}

let currentSelectedPetId = null;

function openPetModal(petId, petName) {
    currentSelectedPetId = petId;
    document.getElementById('modal-pet-name').innerText = petName;
    document.getElementById('pet-action-modal').style.display = 'flex';
}

function closePetModal() {
    document.getElementById('pet-action-modal').style.display = 'none';
    currentSelectedPetId = null;
}

function confirmDelete() {
    if (!currentSelectedPetId) return;
    showAppConfirm(`Are you sure you want to permanently delete this pet?`, async () => {
        try {
            const response = await fetch(`${API_URL}/api/pets/${currentSelectedPetId}`, { method: 'DELETE', headers: getAuthHeaders() });
            if (handleAuthError(response)) return;
            if (response.ok) { 
                closePetModal(); 
                loadUserPets(); 
                showAppAlert("Pet deleted successfully!", "success");
            } 
            else { showAppAlert("Failed to delete pet.", "error"); }
        } catch (error) { showAppAlert("Cannot connect to server.", "error"); }
    });
}

function prepareUpdate() {
    if (!window.currentPets) return;
    const pet = window.currentPets.find(p => p.id == currentSelectedPetId);
    if (!pet) return;

    document.getElementById('pet-action-modal').style.display = 'none';
    document.getElementById('update-pet-name').value = pet.name;
    document.getElementById('update-pet-age').value = pet.age;
    
    document.getElementById('update-pet-type').value = pet.pet_type;
    updateBreedsForEdit();

    // Check if pet.breed exists in the dropdown options
    const isStandardBreed = Object.keys(petData[pet.pet_type]).includes(pet.breed) && pet.breed !== 'Other';
    
    if (isStandardBreed) {
        document.getElementById('update-pet-breed').value = pet.breed;
        document.getElementById('custom-update-pet-breed').style.display = 'none';
    } else {
        document.getElementById('update-pet-breed').value = 'Other';
        document.getElementById('custom-update-pet-breed').value = pet.breed;
        document.getElementById('custom-update-pet-breed').style.display = 'block';
    }
    
    document.getElementById('update-pet-modal').style.display = 'flex';
}

async function submitUpdate() {
    const type = document.getElementById('update-pet-type').value;
    let breed = document.getElementById('update-pet-breed').value;
    const name = document.getElementById('update-pet-name').value;
    const age = document.getElementById('update-pet-age').value;

    if (breed === 'Other') {
        breed = document.getElementById('custom-update-pet-breed').value.trim() || 'Other';
    }

    if (!type || !breed || !name.trim() || !age.trim()) { 
        showAppAlert("Please fill out all fields with valid text!", "error"); 
        return; 
    }

    // Strict Age check based on Excel data
    const ageNum = parseFloat(age);
    let maxAge = 60; // Absolute fallback
    
    const lookupBreed = petData[type][breed] ? breed : "Other";
    if (petData[type] && petData[type][lookupBreed]) {
        maxAge = petData[type][lookupBreed];
    }

    if (isNaN(ageNum) || ageNum < 0 || ageNum > maxAge) {
        showAppAlert(`Please enter a realistic age. The maximum age for this breed is approx ${maxAge} years.`, "error"); 
        return;
    }
    
    if (!isNaN(name)) {
        showAppAlert("Pet name cannot be just numbers.", "error");
        return;
    }

    try {
        const response = await fetch(`${API_URL}/api/pets/${currentSelectedPetId}`, {
            method: 'PUT', headers: getAuthHeaders(),
            body: JSON.stringify({ pet_type: type, breed: breed, name: name, age: age })
        });
        if (handleAuthError(response)) return;
        if (response.ok) {
            document.getElementById('update-pet-modal').style.display = 'none';
            loadUserPets(); 
            showAppAlert("Pet details updated!", "success");
        } else { showAppAlert("Failed to update pet.", "error"); }
    } catch (error) { showAppAlert("Cannot connect to server.", "error"); }
}
// ==========================================
// 3. DIET LOGIC
// ==========================================
function handlePetSelection() {
    // Health and allergy dropdowns are now available for all pet types, not just Dogs/Cats.
    const allergySelect = document.getElementById('diet-allergies');
    const healthSelect = document.getElementById('diet-health');
    allergySelect.disabled = false;
    healthSelect.disabled = false;
}

// Reasonable weight ranges (kg) used to sanity-check the entered weight per species.
const petWeightRanges = {
    "Dog": [0.3, 90], "Cat": [0.3, 12], "Bird": [0.01, 2], "Fish": [0.001, 20],
    "Rabbit": [0.3, 10], "Turtle": [0.01, 90], "Other": [0.01, 200]
};

function generateCustomDiet() {
    const petId = document.getElementById('diet-pet-select').value;
    const weight = parseFloat(document.getElementById('diet-weight').value);
    const health = document.getElementById('diet-health').value;
    const allergy = document.getElementById('diet-allergies').value;

    if (!petId || !document.getElementById('diet-weight').value) { showAppAlert("Please select a pet and enter their weight!", "error"); return; }

    const pet = window.currentPets.find(p => p.id == petId);

    const range = petWeightRanges[pet.pet_type] || petWeightRanges["Other"];
    if (isNaN(weight) || weight <= 0 || weight < range[0] || weight > range[1]) {
        showAppAlert(`Please enter a realistic weight for a ${pet.pet_type} (between ${range[0]}kg and ${range[1]}kg). If your pet weighs less than 1kg, enter it as a decimal, e.g. 0.7.`, "error");
        return;
    }
    
    let mainProtein = "Standard Pet Food"; let secondaryItem = "Fresh Water"; let warningText = "Ensure fresh water is always available.";

    if (pet.pet_type === "Dog") {
        mainProtein = "Commercial Kibble (Drools/Pedigree PRO) or Boiled Chicken & Rice"; secondaryItem = "Plain Curd (Dahi) for digestion & Boiled Carrots";
    } else if (pet.pet_type === "Cat") {
        mainProtein = "High-Protein Cat Food (Whiskas/Meat Up) or Boiled Fish"; secondaryItem = "Unseasoned Chicken Broth"; warningText = "Cats are obligate carnivores. Never give them milk (causes diarrhea).";
    } else if (pet.pet_type === "Bird") {
        mainProtein = "Local Seed Mix (Kangni/Foxtail Millet, Bajra, Sunflower)"; secondaryItem = "Fresh Guava, Papaya, and Coriander (Dhania) leaves"; warningText = "Strictly avoid avocado, apple seeds, and salty snacks.";
    } else if (pet.pet_type === "Fish") {
        mainProtein = "Quality Pellets/Flakes (e.g., Optimum, Taiyo)"; secondaryItem = "Treats: Blanched Peas (Omnivores) or Bloodworms (Carnivores)"; warningText = "The 2-Minute Rule: Only feed what they eat in 2 mins to prevent ammonia spikes.";
    } else if (pet.pet_type === "Rabbit") {
        mainProtein = "Fresh Local Grass (Doob) or Imported Hay (80% of diet)"; secondaryItem = "Coriander (Dhania), Mint (Pudina), & small carrot pieces"; warningText = "Avoid iceberg lettuce, cabbage, and high-sugar fruits.";
    } else if (pet.pet_type === "Turtle") {
        mainProtein = "Aquatic Turtle Pellets (Taiyo/Drools)"; secondaryItem = "Safe Greens & Cuttlebone for Calcium"; warningText = "Avoid spinach and cabbage. Feed adults 3-5 times a week.";
    }

    if (allergy === "Poultry") { mainProtein = "Mutton/Fish-based Kibble (e.g., Himalaya Healthy Pet) or Soy chunks"; } 
    else if (allergy === "Beef") { mainProtein = "Chicken or Egg-based diet (Boiled Eggs & Rice)"; } 
    else if (allergy === "Grains") { mainProtein = "Grain-Free Kibble or Sweet Potato & Meat"; }

    if (health === "Overweight") { secondaryItem = "Bottle Gourd & Pumpkin mixed in food for high fiber"; warningText = "Strictly limit treats and biscuits to less than 5% of daily intake."; } 
    else if (health === "Joints") { secondaryItem = "Cod Liver Oil capsules & Calcium supplements"; } 
    else if (health === "Kidney") { mainProtein = "Vet-prescribed Renal Diet (Low Phosphorus)"; secondaryItem = "Extra Moisture (Mix warm water into dry food)"; }

    let scheduleHTML = "";
    const isBaby = pet.age.toLowerCase().includes("month");

    if (pet.pet_type === "Dog" || pet.pet_type === "Cat") {
        if (isBaby) {
            scheduleHTML = `<div style="margin-bottom: 12px;"><strong style="color: var(--blue);"> Morning (8:00 AM):</strong><br> 40% of daily ${mainProtein}</div>
                            <div style="margin-bottom: 12px;"><strong style="color: var(--amber-dark);"> Afternoon (1:00 PM):</strong><br> 20% of daily ${mainProtein} + ${secondaryItem}</div>
                            <div style="margin-bottom: 8px;"><strong style="color: var(--blue-dark);"> Evening (7:00 PM):</strong><br> 40% of daily ${mainProtein}</div>`;
        } else {
            scheduleHTML = `<div style="margin-bottom: 12px;"><strong style="color: var(--blue);">Morning (8:00 AM):</strong><br> 50% of daily ${mainProtein} + Half of ${secondaryItem}</div>
                            <div style="margin-bottom: 8px;"><strong style="color: var(--blue-dark);">Evening (7:00 PM):</strong><br> 50% of daily ${mainProtein} + Remaining ${secondaryItem}</div>`;
        }
    } else if (pet.pet_type === "Bird" || pet.pet_type === "Rabbit") {
        scheduleHTML = `<div style="margin-bottom: 12px;"><strong style="color: var(--blue);">Morning:</strong><br> Provide fresh ${mainProtein} for the entire day.</div>
                        <div style="margin-bottom: 8px;"><strong style="color: var(--blue-dark);">Evening:</strong><br> Offer ${secondaryItem} as a fresh supplement. Remove uneaten fresh food before night.</div>`;
    } else if (pet.pet_type === "Fish") {
         scheduleHTML = `<div style="margin-bottom: 12px;"><strong style="color: var(--blue);">Morning:</strong><br> Feed ${mainProtein}. Only what they eat in 2 minutes!</div>
                         <div style="margin-bottom: 8px;"><strong style="color: var(--blue-dark);">Evening (Optional Treat):</strong><br> Offer ${secondaryItem} only 2-3 times a week.</div>`;
    } else if (pet.pet_type === "Turtle") {
         scheduleHTML = `<div style="margin-bottom: 12px;"><strong style="color: var(--amber-dark);">Mid-Day (12:00 PM):</strong><br> Feed ${mainProtein} (when the water is warmest for digestion).</div>
                         <div style="margin-bottom: 8px;"><strong style="color: var(--blue-dark);">Supplement:</strong><br> Leave ${secondaryItem} in the tank for grazing.</div>`;
    }

    const resultsCard = document.getElementById('diet-output-card');
    resultsCard.innerHTML = `
        <h3 style="color: var(--pawcare-brand); margin-bottom: 15px; font-family: var(--font-display); border-bottom: 1px solid var(--border); padding-bottom: 10px;">
           Daily Schedule for ${pet.name}
        </h3>
        <div style="font-size: 14.5px; color: var(--text); padding-bottom: 15px; line-height: 1.4;">${scheduleHTML}</div>
        <div style="font-size: 13px; color: #b45309; background: #fef3c7; padding: 12px; border-radius: var(--radius-sm); border: 1px solid #fde68a;">
            <i class="ti ti-stethoscope" style="font-size: 16px; vertical-align: middle;"></i> <strong>Vet Note:</strong> ${warningText}
        </div>
    `;
    document.getElementById('diet-results-section').style.display = 'block';
}

// ==========================================
// 4. VACCINATION LOGIC & DATABASE SYNC
// ==========================================

let appVaccines = [];
let currentVaxPetId = null;

const vaxGuidelines = {
    "Dog": "<strong>Core Vaccines:</strong> Rabies, DHPP (Distemper, Parvovirus, Adenovirus, Parainfluenza).<br><em>Optional:</em> Leptospirosis if outdoors.",
    "Cat": "<strong>Core Vaccines:</strong> Rabies, FVRCP (Feline Viral Rhinotracheitis, Calicivirus, Panleukopenia).",
    "Rabbit": "<strong>Recommendations:</strong> Check local laws for RHDV (Rabbit Hemorrhagic Disease) vaccines.",
    "Bird": "Vaccines are rarely required for indoor birds. Consult an exotic vet.",
    "Fish": "Vaccines do not apply. Maintain water parameters and test ammonia levels.",
    "Turtle": "Vaccines do not apply. Focus on UV-B lighting and water filtration.",
    "Other": "Consult your local veterinarian for species-specific guidelines."
};

async function loadVaccines() {
    const email = localStorage.getItem('pawcare_user_email');
    try {
        const response = await fetch(`${API_URL}/api/vaccines/${email}`, { headers: getAuthHeaders() });
        if (handleAuthError(response)) return;
        if (response.ok) {
            const data = await response.json();
            appVaccines = data.map(v => ({
                id: v.id,
                petId: v.pet_id || v.petId, 
                name: v.name,
                date: v.date
            }));
            
            if (currentVaxPetId) renderPetVaxSchedule(); 
            initVaxTab(); 
            updateGlobalAlerts();
        }
    } catch (error) {
        console.error("Failed to load vaccines from database:", error);
    }
}

async function saveNewVaccine() {
    const name = document.getElementById('new-vax-name').value;
    const date = document.getElementById('new-vax-date').value;
    const email = localStorage.getItem('pawcare_user_email');
    
    if (!name || !date) { showAppAlert("Please fill out the vaccine name and date!", "error"); return; }

    try {
        const response = await fetch(`${API_URL}/api/vaccines`, {
            method: 'POST', 
            headers: getAuthHeaders(),
            body: JSON.stringify({ pet_id: currentVaxPetId, name: name, date: date, owner_email: email })
        });
        if (handleAuthError(response)) return;
        
        if (response.ok) {
            closeAddVaxForm();
            loadVaccines(); 
            showAppAlert("Vaccine recorded!", "success");
        } else { showAppAlert("Failed to save vaccine.", "error"); }
    } catch (error) { showAppAlert("Cannot connect to server.", "error"); }
}

async function markVaxComplete(vaxId) {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    
    const year = yesterday.getFullYear();
    const month = String(yesterday.getMonth() + 1).padStart(2, '0');
    const day = String(yesterday.getDate()).padStart(2, '0');
    const newDate = `${year}-${month}-${day}`;
    
    try {
        const response = await fetch(`${API_URL}/api/vaccines/${vaxId}`, {
            method: 'PUT',
            headers: getAuthHeaders(),
            body: JSON.stringify({ date: newDate }) 
        });
        if (handleAuthError(response)) return;

        if (response.ok) {
            loadVaccines(); 
            showAppAlert("Marked as Completed!", "success");
        } else { showAppAlert("Failed to update record.", "error"); }
    } catch (error) { showAppAlert("Cannot connect to server.", "error"); }
}

function deleteVax(vaxId) {
    showAppConfirm("Delete this record permanently?", async () => {
        try {
            const response = await fetch(`${API_URL}/api/vaccines/${vaxId}`, { method: 'DELETE', headers: getAuthHeaders() });
            if (handleAuthError(response)) return;
            if (response.ok) {
                loadVaccines(); 
                showAppAlert("Vaccine record deleted.", "success");
            } else { showAppAlert("Failed to delete record.", "error"); }
        } catch (error) { showAppAlert("Cannot connect to server.", "error"); }
    });
}

function calculateVaxStatus(dateString) {
    const today = new Date(); 
    today.setHours(0, 0, 0, 0); 
    
    const parts = dateString.split('-');
    const vaxDate = new Date(parts[0], parts[1] - 1, parts[2]); 
    
    const diffTime = vaxDate - today;
    const diffDays = Math.round(diffTime / (1000 * 60 * 60 * 24));
    
    if (diffDays < 0) return { category: 'completed', text: 'Done', color: 'var(--blue)' };
    if (diffDays >= 0 && diffDays <= 14) return { category: 'due', text: 'Due Soon', color: 'var(--amber-dark)' };
    return { category: 'upcoming', text: 'Upcoming', color: 'var(--text-3)' };
}

function initVaxTab() {
    const listContainer = document.getElementById('vax-pet-list');
    listContainer.innerHTML = '';
    
    if (!window.currentPets || window.currentPets.length === 0) {
        listContainer.innerHTML = '<p style="text-align: center; color: var(--text-3);">No pets found. Add a pet on the Home screen!</p>';
        return;
    }

window.currentPets.forEach(pet => {
        const petVaxes = appVaccines.filter(v => v.petId == pet.id);
        
        // CRITICAL FIX: Safely escape the name for the HTML attribute
        const safeVaxName = escapeHTML(pet.name).replace(/'/g, "\\'").replace(/"/g, "&quot;");
        
        listContainer.innerHTML += `
            <div class="card" style="padding: 15px; cursor: pointer; display: flex; justify-content: space-between; align-items: center;" onclick="openVaxDetails(${parseInt(pet.id)}, '${safeVaxName}')">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="width: 40px; height: 40px; background: var(--blue-light); color: var(--blue); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 20px;">
                        <i class="ti ti-vaccine"></i>
                    </div>
                    <div>
                        <div style="font-weight: 600; color: var(--text); font-size: 16px;">${escapeHTML(pet.name)}</div>
                        <div style="font-size: 12px; color: var(--text-2);">${petVaxes.length} Records</div>
                    </div>
                </div>
                <i class="ti ti-chevron-right" style="color: var(--text-3);"></i>
            </div>
        `;
    });
}

function openVaxDetails(petId, petName) {
    currentVaxPetId = petId;
    document.getElementById('vax-main-view').style.display = 'none';
    document.getElementById('vax-detail-view').style.display = 'flex';
    document.getElementById('vax-detail-title').innerText = petName;
    
    const pet = window.currentPets.find(p => p.id == petId);
    document.getElementById('vax-recommendation-text').innerHTML = vaxGuidelines[pet.pet_type] || vaxGuidelines["Other"];
    
    const isNonVaxPet = ["Fish", "Turtle", "Bird"].includes(pet.pet_type);
    
    if (isNonVaxPet) {
        document.getElementById('vax-stats-container').style.display = 'none';
        document.getElementById('vax-add-btn').style.display = 'none';
        document.getElementById('vax-schedule-list').innerHTML = `
            <div style="text-align: center; padding: 30px 20px; background: var(--surface); border-radius: var(--radius-sm); border: 1px solid var(--border);">
                <i class="ti ti-shield-check" style="font-size: 32px; color: var(--text-3); margin-bottom: 10px;"></i>
                <div style="color: var(--text-2); font-size: 14px;">Vaccination tracking is not required for this species.</div>
            </div>
        `;
    } else {
        document.getElementById('vax-stats-container').style.display = 'flex';
        document.getElementById('vax-add-btn').style.display = 'block';
        renderPetVaxSchedule();
    }
}

function renderPetVaxSchedule() {
    const petVaxes = appVaccines.filter(v => v.petId == currentVaxPetId);
    const listContainer = document.getElementById('vax-schedule-list');
    listContainer.innerHTML = '';

    let countCompleted = 0, countDue = 0, countUpcoming = 0;

    if (petVaxes.length === 0) {
        listContainer.innerHTML = '<p style="text-align: center; color: var(--text-3); font-size: 13px;">No vaccines recorded yet.</p>';
    } else {
        petVaxes.sort((a, b) => new Date(a.date) - new Date(b.date));

        petVaxes.forEach(vax => {
            const status = calculateVaxStatus(vax.date);
            if (status.category === 'completed') countCompleted++;
            if (status.category === 'due') countDue++;
            if (status.category === 'upcoming') countUpcoming++;

            listContainer.innerHTML += `
                <div class="card" style="padding: 15px; cursor: pointer; transition: 0.2s;" onclick="toggleVaxActions(${parseInt(vax.id)})">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <div style="font-weight: 600; color: var(--text); font-size: 15px;">${escapeHTML(vax.name)}</div>
                            <div style="font-size: 12px; color: var(--text-2);">${escapeHTML(vax.date)}</div>
                        </div>
                        <div style="font-size: 13px; font-weight: bold; color: ${status.color}; display: flex; align-items: center; gap: 4px;">
                            ${status.text} <i class="ti ti-chevron-down" id="vax-icon-${parseInt(vax.id)}" style="transition: 0.3s; color: var(--text-3);"></i>
                        </div>
                    </div>
                    
                    <div id="vax-actions-${parseInt(vax.id)}" style="display: none; margin-top: 15px; padding-top: 15px; border-top: 1px solid var(--border); gap: 10px;">
                        ${status.category !== 'completed' ? `
                            <button class="btn" style="flex: 1; padding: 8px; font-size: 13px; color: var(--blue); background: transparent; border: none;" onclick="event.stopPropagation(); markVaxComplete(${parseInt(vax.id)})">
                                <i class="ti ti-check"></i> Mark Done
                            </button>
                        ` : ''}
                        <button class="btn" style="flex: 1; padding: 8px; font-size: 13px; color: #ef4444; background: transparent; border: none;" onclick="event.stopPropagation(); deleteVax(${parseInt(vax.id)})">
                            <i class="ti ti-trash"></i> Delete
                        </button>
                    </div>
                </div>
            `;
        });
    }

    document.getElementById('stat-completed').innerText = countCompleted;
    document.getElementById('stat-due').innerText = countDue;
    document.getElementById('stat-upcoming').innerText = countUpcoming;
}

function toggleVaxActions(vaxId) {
    const actionDiv = document.getElementById(`vax-actions-${vaxId}`);
    const icon = document.getElementById(`vax-icon-${vaxId}`);
    
    if (actionDiv.style.display === 'none') {
        actionDiv.style.display = 'flex';
        icon.style.transform = 'rotate(180deg)';
    } else {
        actionDiv.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';
    }
}

function closeVaxDetails() {
    document.getElementById('vax-detail-view').style.display = 'none';
    document.getElementById('vax-main-view').style.display = 'block';
    currentVaxPetId = null;
    initVaxTab(); 
}

function openAddVaxForm() {
    document.getElementById('vax-detail-view').style.display = 'none';
    document.getElementById('vax-add-view').style.display = 'flex';
    document.getElementById('new-vax-name').value = '';
    document.getElementById('new-vax-date').value = '';
}

function closeAddVaxForm() {
    document.getElementById('vax-add-view').style.display = 'none';
    document.getElementById('vax-detail-view').style.display = 'flex';
}

function updateGlobalAlerts() {
    const notifList = document.getElementById('notification-list');
    notifList.innerHTML = '';
    let totalDueSoon = 0;

    appVaccines.forEach(vax => {
        const status = calculateVaxStatus(vax.date);
        if (status.category === 'due') {
            totalDueSoon++;
            const pet = window.currentPets?.find(p => p.id == vax.petId);
            const petName = pet ? pet.name : "your pet";

            notifList.innerHTML += `
                <div class="card" style="padding: 15px; border-left: 4px solid var(--amber);">
                    <div style="display: flex; gap: 12px; align-items: center;">
                        <div style="width: 35px; height: 35px; border-radius: 50%; background: var(--amber-light); color: var(--amber-dark); display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0;">
                            <i class="ti ti-bell-ringing"></i>
                        </div>
                        <div>
                            <div style="font-size: 14px; color: var(--text); line-height: 1.4;">You have an upcoming vaccine <strong>${escapeHTML(vax.name)}</strong> for <strong>${escapeHTML(petName)}</strong>.</div>
                            <div style="font-size: 11px; color: var(--text-2); margin-top: 4px;">Due by ${escapeHTML(vax.date)}</div>
                        </div>
                    </div>
                </div>
            `;
        }
    });

    if (totalDueSoon === 0) {
        notifList.innerHTML = `
            <div style="text-align: center; padding: 40px 20px;">
                <i class="ti ti-bell-z" style="font-size: 40px; color: var(--text-3); margin-bottom: 10px;"></i>
                <div style="color: var(--text-2); font-size: 14px;">You have no new notifications.</div>
            </div>
        `;
    }

    const badge = document.getElementById('alert-badge');
    if (totalDueSoon > 0) {
        badge.innerText = totalDueSoon;
        badge.style.display = 'block';
    } else {
        badge.style.display = 'none';
    }
}

function openNotifications() {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('notifications-view').classList.add('active');
}

// ==========================================
// 5. DIAGNOSTIC HEALTH ASSISTANT
// ==========================================
let cameraStream = null;
let activeAnalysisData = { type: null, value: null }; 

function switchDetectMode(mode) {
    if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
        cameraStream = null;
    }

    document.getElementById('detect-cam-container').style.display = mode === 'camera' ? 'flex' : 'none';
    document.getElementById('detect-upload-container').style.display = mode === 'upload' ? 'block' : 'none';
    document.getElementById('detect-text-container').style.display = mode === 'text' ? 'block' : 'none';

    const btnCam = document.getElementById('tab-cam');
    const btnUpload = document.getElementById('tab-upload');
    const btnText = document.getElementById('tab-text');

    btnCam.className = mode === 'camera' ? 'btn btn-primary' : 'btn btn-outline';
    btnUpload.className = mode === 'upload' ? 'btn btn-primary' : 'btn btn-outline';
    btnText.className = mode === 'text' ? 'btn btn-primary' : 'btn btn-outline';

    document.getElementById('ai-results-section').style.display = 'none';
}

async function startCamera() {
    const video = document.getElementById('camera-feed');
    const snapshot = document.getElementById('camera-snapshot');
    const btnStart = document.getElementById('btn-start-camera');
    const btnTake = document.getElementById('btn-take-photo');
    const btnRetake = document.getElementById('btn-retake-photo');
    document.getElementById('ai-results-section').style.display = 'none';

    snapshot.style.display = 'none';

    try {
        cameraStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: 'environment' }
        });
        video.srcObject = cameraStream;
        video.style.display = 'block';

        btnStart.style.display = 'none';
        btnTake.style.display = 'block';
        btnRetake.style.display = 'none';
    } catch (err) {
        console.error("Camera access denied:", err);
        showAppAlert("Could not access camera. Please double check app permissions.", "error");
    }
}

function takePhoto() {
    const video = document.getElementById('camera-feed');
    const canvas = document.getElementById('camera-canvas');
    const snapshot = document.getElementById('camera-snapshot');
    const btnTake = document.getElementById('btn-take-photo');
    const btnRetake = document.getElementById('btn-retake-photo');

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);

    const imageDataUrl = canvas.toDataURL('image/jpeg', 0.8);
    snapshot.src = imageDataUrl;
    snapshot.style.display = 'block';
    video.style.display = 'none';

    if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
    }

    btnTake.style.display = 'none';
    btnRetake.style.display = 'block';

    activeAnalysisData = { type: 'image', value: imageDataUrl };
    showSubmitButton();
}

function retakePhoto() {
    startCamera();
}

function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = function(e) {
        const previewContainer = document.getElementById('upload-preview-container');
        const previewImg = document.getElementById('upload-preview');
        
        previewImg.src = e.target.result;
        previewContainer.style.display = 'block';

        activeAnalysisData = { type: 'image', value: e.target.result };
        showSubmitButton();
    };
    reader.readAsDataURL(file);
}

function sendSymptomsToAI() {
    const text = document.getElementById('detect-symptoms-input').value.trim();
    if (!text) {
        showAppAlert("Please input your pet's symptoms first.", "error");
        return;
    }
    activeAnalysisData = { type: 'text', value: text };
    executeBackendDiagnostic();
}

function showSubmitButton() {
    const resultsSection = document.getElementById('ai-results-section');
    const aiOutput = document.getElementById('detect-result-display');

    resultsSection.style.display = 'block';
    aiOutput.innerHTML = `
      <div style="text-align:center; padding: 15px 5px;">
          <div style="color: var(--text-2); margin-bottom: 12px; font-size:13px;">Image ready for screening.</div>
          <button class="btn btn-primary" style="width: 100%;" onclick="executeBackendDiagnostic()">Run Medical Diagnostic</button>
      </div>
    `;
}

async function executeBackendDiagnostic() {
    const petId = document.getElementById('detect-pet-select').value;
    if (!petId) {
        showAppAlert("Please select which pet needs a checkup first!", "error");
        return;
    }

    const selectedPet = window.currentPets.find(p => p.id == petId);
    
    activeAnalysisData.petContext = {
        name: selectedPet.name,
        species: selectedPet.pet_type,
        breed: selectedPet.breed,
        age: selectedPet.age
    };

    const resultsSection = document.getElementById('ai-results-section');
    const aiOutput = document.getElementById('detect-result-display');

    resultsSection.style.display = 'block';
    aiOutput.innerHTML = `
        <div style="text-align:center; padding: 25px 0;">
            <i class="ti ti-loader ti-spin" style="font-size: 32px; color: var(--blue); margin-bottom: 12px; display:inline-block;"></i>
            <div style="font-weight:500;">Checking ${selectedPet.name}'s symptoms...</div>
            <div style="font-size:11px; color:var(--text-3); margin-top:4px;">Please wait a moment...</div>
        </div>
    `;

    try {
        const response = await fetch(`${API_URL}/api/analyze-health`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(activeAnalysisData) 
        });
        if (handleAuthError(response)) return;

        if (!response.ok) throw new Error("API Diagnostic analysis failed");
        
        const data = await response.json();
        // Escape raw AI text FIRST to prevent any HTML injection, then apply safe formatting
        let cleanText = escapeHTML(data.analysis);

        let cardBg = "#ffffff";
        let cardBorder = "#e2e8f0";
        let iconColor = "var(--blue)";
        let titleColor = "var(--blue-dark)";
        let titleIcon = "ti-stethoscope";

        const textLower = cleanText.toLowerCase();
        
        if (textLower.includes("severe") || textLower.includes("emergency")) {
            cardBg = "#fef2f2";       
            cardBorder = "#fca5a5";   
            iconColor = "#dc2626";    
            titleColor = "#991b1b";   
            titleIcon = "ti-alert-triangle";
        } else if (textLower.includes("moderate")) {
            cardBg = "#fffbeb";       
            cardBorder = "#fcd34d";   
            iconColor = "#d97706";    
            titleColor = "#92400e";   
            titleIcon = "ti-alert-circle"; 
        } else if (textLower.includes("mild")) {
            cardBg = "#f0fdf4";       
            cardBorder = "#86efac";   
            iconColor = "#16a34a";    
            titleColor = "#166534";   
            titleIcon = "ti-shield-check";
        }
        
        cleanText = cleanText.replace(/^[\s]*\-[\s]+(.*)/gm, `<div style="display: flex; margin-bottom: 10px;"><span style="color: ${iconColor}; margin-right: 10px; font-weight: bold; font-size: 16px;">•</span><span>$1</span></div>`);
        cleanText = cleanText.replace(/\n/g, '<br>');
        cleanText = cleanText.replace(/(<br>){2,}/g, '<br><br>');
        cleanText = cleanText.replace(/<\/div><br>/g, '</div>');
        
        aiOutput.innerHTML = `
            <div style="background: ${cardBg}; padding: 20px; border-radius: 12px; border: 2px solid ${cardBorder}; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
                <h4 style="color: ${titleColor}; margin-top: 0; margin-bottom: 15px; font-size: 16px; border-bottom: 1px solid ${cardBorder}; padding-bottom: 10px;">
                    <i class="ti ${titleIcon}" style="color: ${iconColor}; font-size: 18px; vertical-align: bottom;"></i> 
                    <span>AI Diagnostic Report</span>
                </h4>
                <div style="font-size: 15px; line-height: 1.6; color: #334155; font-weight: bold;">
                    ${cleanText}
                </div>
            </div>
        `;

    } catch (error) {
        console.error(error);
        aiOutput.innerHTML = `
            <div style="color: #dc2626; padding: 10px; text-align:center;">
                <i class="ti ti-alert-triangle" style="font-size:24px;"></i>
                <div style="margin-top:5px; font-weight:600;">Diagnostic Evaluation Failed</div>
                <div style="font-size:12px; opacity:0.8;">There is an issue at our end.</div>
            </div>`;
    }
}

function analyzePetHealth() {
    const textInput = document.getElementById('detect-symptoms-input');
    
    if (textInput && textInput.value.trim() !== "") {
        activeAnalysisData = { type: 'text', value: textInput.value.trim() };
        executeBackendDiagnostic();
    } else {
        showAppAlert("Please describe the symptoms first.", "error");
    }
}

// ==========================================
// 6. CLINIC LOCATOR (LIVE GPS DATA)
// ==========================================
let mapInitialized = false;
let vetMap;
let markersLayer = new L.LayerGroup(); 

function initMap() {
    if (mapInitialized) {
        vetMap.invalidateSize(); 
        return; 
    }
    
    const defaultLat = 17.7134;
    const defaultLng = 83.1645;

    vetMap = L.map('map').setView([defaultLat, defaultLng], 12);
    markersLayer.addTo(vetMap);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors'
    }).addTo(vetMap);

    let userMarker = null;

    function setUserLocation(lat, lng, isGPS = false) {
        vetMap.setView([lat, lng], 13);
        
        if (userMarker) {
            vetMap.removeLayer(userMarker);
        }
        
        userMarker = L.circleMarker([lat, lng], {
            color: 'white', fillColor: '#2563eb', fillOpacity: 1, radius: 8, weight: 2
        }).addTo(vetMap).bindPopup(isGPS ? "<b>Your Current Location</b>" : "<b>Default Location</b>").openPopup();

        generatePresentationClinics(lat, lng);
    }

    const clinicListContainer = document.getElementById('clinic-list');
    clinicListContainer.innerHTML = '<div style="text-align: center; padding: 20px; color: var(--text-2);"><i class="ti ti-loader ti-spin" style="font-size: 24px; color: var(--blue); margin-bottom: 8px;"></i><br>Finding nearby clinics...</div>';

    if ("geolocation" in navigator) {
        navigator.geolocation.getCurrentPosition(
            (position) => {
                setUserLocation(position.coords.latitude, position.coords.longitude, true);
            },
            (error) => {
                console.log("GPS access denied or unavailable. Using fallback location.");
                setUserLocation(defaultLat, defaultLng, false);
            },
            { enableHighAccuracy: true, timeout: 5000 }
        );
    } else {
        setUserLocation(defaultLat, defaultLng, false);
    }

    mapInitialized = true;
}

function calculateDistance(lat1, lon1, lat2, lon2) {
    const R = 6371; 
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return (R * c).toFixed(1);
}

let pendingNavUrl = "";

function triggerNavigation(url, clinicName) {
    pendingNavUrl = url;
    let modal = document.getElementById('custom-nav-modal');
    
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'custom-nav-modal';
        
        modal.style.position = 'fixed';
        modal.style.top = '0';
        modal.style.left = '0';
        modal.style.width = '100vw';
        modal.style.height = '100vh';
        modal.style.backgroundColor = 'rgba(0, 0, 0, 0.7)'; 
        modal.style.backdropFilter = 'blur(3px)'; 
        modal.style.display = 'flex';
        modal.style.alignItems = 'center'; 
        modal.style.justifyContent = 'center'; 
        modal.style.zIndex = '99999'; 
        
        modal.innerHTML = `
            <div class="card" style="width: 85%; max-width: 350px; background: var(--surface); padding: 25px 20px; border: 2px solid var(--blue); border-radius: var(--radius-md); text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
                
                <div style="width: 65px; height: 65px; background: rgba(37, 99, 235, 0.15); color: var(--blue); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 32px; margin: 0 auto 15px;">
                    <i class="ti ti-map-route"></i>
                </div>
                
                <h3 style="margin: 0 0 10px 0; font-family: var(--font-display); color: var(--text); font-size: 20px;">Open Google Maps?</h3>
                
                <p style="color: var(--text-2); font-size: 14px; margin-bottom: 25px; line-height: 1.5;">
                    You are about to leave PetCare to get live driving directions to:<br>
                    <strong id="nav-clinic-name" style="color: var(--blue); font-size: 16px; display: inline-block; margin-top: 8px;">Clinic Name</strong>
                </p>
                
                <div style="display: flex; gap: 12px;">
                    <button class="btn btn-outline" style="flex: 1; padding: 12px; font-weight: bold;" onclick="closeNavModal()">Cancel</button>
                    <button class="btn btn-primary" style="flex: 1; padding: 12px; display: flex; justify-content: center; align-items: center; gap: 6px; font-weight: bold;" onclick="confirmNavigation()">
                        <i class="ti ti-external-link"></i> Let's Go
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    } else {
        modal.style.display = 'flex';
    }
    
    document.getElementById('nav-clinic-name').innerText = clinicName;
}

function closeNavModal() {
    const modal = document.getElementById('custom-nav-modal');
    if (modal) modal.style.display = 'none';
    pendingNavUrl = "";
}

function confirmNavigation() {
    if (pendingNavUrl) {
        window.open(pendingNavUrl, '_blank');
        closeNavModal();
    }
}

function generatePresentationClinics(centerLat, centerLng) {
    const clinicListContainer = document.getElementById('clinic-list');
    clinicListContainer.innerHTML = ''; 
    markersLayer.clearLayers();

    let clinics = [
        { name: "La vet i Animal Clinic", area: "Duvvada", lat: 17.6985065, lon: 83.1576402, rating: "4.1", reviews: 231 },
        { name: "Happy Tails ANIMAL HOSPITAL & PET SPA", area: "Gajuwaka", lat: 17.6782028, lon: 83.178512, rating: "4.8", reviews: 429 },
        { name: "The VIZAG VET ANIMAL HOSPITAL & PET SPA", area: "Gajuwaka", lat: 17.6778751, lon: 83.1972249, rating: "4.5", reviews: 98 },
        { name: "B2Vet Pet Hospital", area: "Gajuwaka", lat: 17.679903, lon: 83.2029292, rating: "5.0", reviews: 249 },
        { name: "Ganesh Pet Clinic", area: "Aganampudi", lat: 17.6856247, lon: 83.1214656, rating: "4.5", reviews: 55 },
        { name: "VET AND PET CARE", area: "Vadlapudi", lat: 17.691002, lon: 83.1712739, rating: "2.6", reviews: 5 },
        { name: "Star Breeds Pet Store and Clinic", area: "Gajuwaka", lat: 17.6800895, lon: 83.1969448, rating: "4.9", reviews: 112 },
        { name: "Marshalls Pet Zone and Pet Spa", area: "Kurmannapalem", lat: 17.6855878, lon: 83.1669058, rating: "4.8", reviews: 340 }
    ];

    clinics.forEach(clinic => {
        clinic.distance = calculateDistance(centerLat, centerLng, clinic.lat, clinic.lon);
    });
    clinics.sort((a, b) => parseFloat(a.distance) - parseFloat(b.distance));

    const vetIcon = L.divIcon({
        className: 'custom-div-icon',
        html: `<div style="background-color: #ef4444; color: white; width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 2px solid white; box-shadow: 0 2px 5px rgba(0,0,0,0.3);"><i class="ti ti-building-hospital" style="font-size: 18px;"></i></div>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15]
    });

    clinics.forEach(clinic => {
        const marker = L.marker([clinic.lat, clinic.lon], { icon: vetIcon }).addTo(markersLayer);
        
        const searchQuery = `${clinic.name} ${clinic.area} Visakhapatnam`;
        const googleMapsLink = `https://maps.google.com/?q=${encodeURIComponent(searchQuery)}&travelmode=driving`;
        
        marker.bindPopup(`<b>${clinic.name}</b><br>⭐ ${clinic.rating}`);

        clinicListContainer.innerHTML += `
            <div class="card" style="padding: 15px; border: 1px solid var(--border); border-radius: var(--radius-md);">
                <div style="font-weight: 600; font-size: 15px; color: var(--text); margin-bottom: 4px;">${clinic.name}</div>
                <div style="font-size: 12px; color: var(--blue); margin-bottom: 8px;"><i class="ti ti-map-pin"></i> ${clinic.area}</div>
                
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; margin-bottom: 15px;">
                    <span style="color: var(--text-2);"><i class="ti ti-route" style="vertical-align: middle;"></i> ${clinic.distance} km away</span>
                    <span style="color: var(--amber-dark); font-weight: bold;">⭐ ${clinic.rating} <span style="font-weight: normal; color: var(--text-3);">(${clinic.reviews})</span></span>
                </div>
                
                <div style="display: flex; gap: 10px;">
                    <button class="btn btn-primary" style="width: 100%; padding: 10px; font-size: 14px; text-align: center; display: flex; align-items: center; justify-content: center; gap: 8px;" onclick="triggerNavigation('${googleMapsLink}', '${clinic.name}')">
                        <i class="ti ti-location-share" style="font-size: 18px;"></i> Start Navigation
                    </button>
                </div>
            </div>
        `;
    });
}

// ==========================================
// 7. PROFILE & DATABASE UPDATES
// ==========================================
function deleteAccount() {
    showAppConfirm("Delete your account permanently?", async () => {
        const userEmail = localStorage.getItem('pawcare_user_email');
        
        try {
            const response = await fetch(`${API_URL}/api/users/${userEmail}`, {
                method: 'DELETE',
                headers: getAuthHeaders()
            });
            
            if (handleAuthError(response)) return;
            
            if (response.ok) {
                // Instantly wipe local storage and kick them to login
                localStorage.removeItem('pawcare_user_name');
                localStorage.removeItem('pawcare_user_email');
                localStorage.removeItem('pawcare_auth_token');
                window.location.href = "login.html";
            } else {
                showAppAlert("Failed to delete account.", "error");
            }
        } catch (error) {
            showAppAlert("Cannot connect to server.", "error");
        }
    });
}

function editUsername() {
  const currentName = localStorage.getItem('pawcare_user_name');
  document.getElementById('new-username-input').value = currentName || '';
  document.getElementById('username-error').style.display = 'none';
  document.getElementById('edit-username-modal').style.display = 'flex';
}

function closeEditUsernameModal() {
  document.getElementById('edit-username-modal').style.display = 'none';
}

async function saveNewUsername() {
  const newName = document.getElementById('new-username-input').value.trim();
  const currentName = localStorage.getItem('pawcare_user_name');
  const userEmail = localStorage.getItem('pawcare_user_email'); 
  const errorText = document.getElementById('username-error');

  if (!newName) {
      errorText.style.display = 'block';
      return;
  }

  if (newName !== currentName) {
      const btn = document.querySelector('#edit-username-modal .btn-primary');
      const originalText = btn.innerText;
      btn.innerHTML = '<i class="ti ti-loader ti-spin"></i> Saving...';
      btn.disabled = true;

      try {
          const response = await fetch(`${API_URL}/api/users/${userEmail}/username`, {
              method: 'PUT',
              headers: getAuthHeaders(),
              body: JSON.stringify({ new_username: newName })
          });
          if (handleAuthError(response)) return;

          if (response.ok) {
              localStorage.setItem('pawcare_user_name', newName);
              document.getElementById('profile-name').innerText = newName;
              document.getElementById('profile-initial').innerText = newName.charAt(0).toUpperCase();
              closeEditUsernameModal();
              showAppAlert("Username successfully updated!", "success");
          } else {
              showAppAlert("Failed to update username.", "error");
          }
      } catch (error) {
          showAppAlert("Cannot connect to server. Is FastAPI running?", "error");
      } finally {
          btn.innerHTML = originalText;
          btn.disabled = false;
      }
  } else {
      closeEditUsernameModal(); 
  }
}
