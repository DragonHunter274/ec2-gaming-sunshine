{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    python313
    pre-commit
    commitizen
  ];

  shellHook = ''
    if [ ! -d "venv" ]; then
      echo "Creating Python virtual environment..."
      python -m venv venv
    fi
    source venv/bin/activate
    pip install -r requirements.txt --quiet
  '';
}
