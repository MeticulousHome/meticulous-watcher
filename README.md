
# meticulous-watcher

A supervisory service that will handle software updates and notify the front-end if the back-end service fails executing

---

## Firmware update through Frontend APP

### Prepare the update package

Preparation of a package is not needed for the end user, but for the developing team

The update package (from now on referred simply as 'package') is a compressed file that holds the data that is going to be updated as well as the Script in charge of doing the update.

#### GENERAL STRUCTURE

- All other folders must be contained in a folder with the name of "meticulous"
- Inside that folder we will create another with the name "library"

``` none
meticulous/
├── program_esp32_via_raspi/
├── backend_for_esp32/
├── lcd_update/
├── meticulous-frontend/
├── library/
├── UpdateScript/
```

##### meticulous frontend

- Clone the repository of meticulous-frontend (`git@github.com:FFFuego/meticulous-frontend.git`) corresponding to the update. It doesn't matter where it's cloned
- Copy the folder created to the folder "meticulous/"
- Using a terminal run the following command while being in the repo directory:

```sh
npm install
```

- It will take a few minutes and upon completation, you will be able to see a new folder `./node_modules`

- In the same directory run the following command:

```sh
npm run build
```

- This will generate a folder of name *build* in which the static files for the server are saved
- Copy the (whole) *build* folder into `<your-path>/meticulous/meticulous-frontend`

##### backend for esp32

- Clone the repository of **backend_for_esp32** (`git@github.com:FFFuego/backend_for_esp32.git`) corresponding to the update
- Copy the folder created to the directory `<your-path>/meticulous`

There are 2 ways to get the libraries needed for the script to run

- using github actions after pushing to `dev`

- download them manually using python
  
  - Get into the repo directory and run the following to create a virtual environment

  ```sh
    python3 -m venv venv
  ```

  - activate the virtual environment running the following commands
    **Linux/Bash:**  

    ```sh
    . venv/bin/activcate
    ```

    **Windows powershell:**

    ```sh
      Set-ExecutionPolicy unrestricted -Scope process
      .\venv\Scripts\activate    
    ```

  - Create a directory to save the libraries ando move into it
  **Linux/Bash:**  

    ```sh
      mkdir -p <directory name> && cd <directory name>
    ```

    **Windows powershell:**

    ```sh
      mkdir -p <directory name>; cd <directory name>    
    ```

  - Download the libraries for the varsom

  ```sh
  pip download  -r ../requirements.txt --platform manylinux_2_17_aarch64 --implementation cp --abi cp39 --no-deps
  ```

  The files download will be wheel type (.whl)
|
  - Copy all the `.whl` files inside `<directory name>` to `meticulous/library`

  ```sh
    cp *.whl <your-path>/meticulous/library
  ```

##### program_esp32_via_raspi

- Clone the repository of **flow_machine_firmware** ( `git@github.com:FFFuego/flow_machine_firmware.git` ). The destination does not matter.

- Open the project in Visual Studio Code to make use of the PIO extension

- Compile the project `CTRL + ALT + B`

- Find the compiled binaries `firmware.bin` and `partitions.bin`, they can be found in the route

```sh
  <path>\flow_machine_firmware\.pio\build\esp32doit-devkit-v1\
```

- Copy both files to `meticulous/program_esp32_via_raspi` manually or by running the following command (assuming your `cwd` is the clone path of `flow_machine_firmware`)

```sh
cp .\.pio\build\esp32doit-devkit-v1\*.bin <your-path>/meticulous/program_esp32_via_raspi
```


##### lcd_update

Go to the [meticulous home ui build distribution page](https://dist.meticuloushome.com/)
Navigate to `dial-app/ >> prod/` and download the package required to update

copy the `.deb` package to `<your-path>/meticulous/lcd_update` and rename the package to  `ui.deb` (including extension)

##### update_script

This Script calls the script located into the directory `<your-path>/meticulous/update_script` wich contains the logic of the update. This script must use pure python with no dependencies as to not have the need to create any extra virtual environments

> ***NOTE: There is no need to have all the folders within the directory but only the ones that require an upadte, e.g if there is only need for a firmware update, the meticulous/ directory may only contain the folder `program_esp32_via_raspi/` and `update_script/`***

---

Once the directory is fulled with the required components, compress it in a `.tar.gz` format by running the following commands within `<your-path>/`

-**Linux/Bash**

```sh
tar -cf update.tar meticulous/ && gzip update.tar
```

This should generate a `update.tar.gz` file in `<your-path>` next to the `meticulous/` folder which now can be sent to the machine using the dashboard interface under the `Settings` menu

> **NOTE: the name of the package is non important as long as it has the correct file type**
